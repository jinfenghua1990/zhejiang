"""吉客云 Web Adapter（V1 主通道）。

复用 hoocy13/jike-cloud-data-pipeline 项目验证过的请求形态：
- cURL 解析（scripts/sync_curl_auth.py）：authorization / cookie / ati / bx-v /
  user-agent / commonVerify，外加从 cURL 学习各数据端点 URL；
- 签名算法：md5(secret + sorted(k+v 非空项) + secret).upper()，
  appkey=jackyun_web_browser_2024，timestamp=毫秒；
- 分页：pageIndex/pageSize，返回行数不足一页即停止；
- 错误分类：NEED_LOGIN / NEED_USER_VERIFY / API / NETWORK / PARSE。

只读约束：本 Adapter 所有方法只做查询，不向吉客云写任何数据。
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode

import requests
from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import decrypt_secret, encrypt_secret
from app.models.integration import IntegrationConnection, IntegrationCredential

WEB_APP_KEY = "jackyun_web_browser_2024"

PROVIDER = "jky_web"
CONNECTION_MODE = "网页登录态"

# 会话状态（落 IntegrationConnection.status）
SESSION_ACTIVE = "connected"
SESSION_NEED_LOGIN = "needs_login"
SESSION_NEED_VERIFY = "needs_user_verify"
SESSION_ERROR = "error"
SESSION_UNCONFIGURED = "unconfigured"


# 各端点默认 URL（cURL 未学习到时兜底；与参考项目一致或按其路径规律推导）。
DEFAULT_ENDPOINTS: dict[str, str] = {
    "stockin": "/jkyun/erp-busiorder/goodsdoc/listGoodsDoc",
    "stockin_detail": "/jkyun/erp-busiorder/goodsdoc/listGoodsDocDetail",
    "total_stock": "/jkyun/erp-stock/stock/allStockSkuList",
    "warehouse_stock": "/jkyun/erp-stock/warehouseStock/stockSkuList",
    "sales_export": "/jkyun/excel-service/manager/startExcelExport",
    "sales_export_validate": "/jkyun/excel-service/manager/validateExcelExport",
    "task_list": "/jkyun/tms/taskmanage/sysTaskInfoList",
}


class JkyWebError(Exception):
    """Web Adapter 统一异常；kind 用于同步状态机判定。"""

    def __init__(self, message: str, kind: str = "api"):
        super().__init__(message)
        self.message = message
        self.kind = kind  # need_login / need_user_verify / api / network / parse / unconfigured


class JkyWebSessionError(JkyWebError):
    """登录态缺失/失效。"""


# ---------------------------------------------------------------------------
# cURL 解析（复用参考项目 sync_curl_auth.py / 入库查询_web.py 的实现形态）
# ---------------------------------------------------------------------------

def normalize_curl_text(text: str) -> str:
    """Windows cmd「Copy as cURL」会带 ^ 换行续行符，先归一化。"""
    return text.replace("^\\r\\n", " ").replace("^\\n", " ").replace("^", "")


def parse_curl_text(raw: str) -> dict[str, Any]:
    tokens = shlex.split(normalize_curl_text(raw), posix=True)
    if not tokens or tokens[0].lower() != "curl":
        # 允许省略开头的 curl 关键字（部分复制习惯）
        if tokens and (tokens[0].startswith("http") or tokens[0].startswith("-")):
            tokens = ["curl"] + tokens
        else:
            raise JkyWebError("输入不像一条 cURL 命令（应以 curl 开头）", "parse")

    url = ""
    headers: dict[str, str] = {}
    cookie = ""
    data_raw = ""
    i = 1
    while i < len(tokens):
        token = tokens[i]
        if token in ("-H", "--header"):
            i += 1
            if i < len(tokens) and ":" in tokens[i]:
                name, value = tokens[i].split(":", 1)
                headers[name.strip().lower()] = value.strip()
        elif token in ("-b", "--cookie"):
            i += 1
            if i < len(tokens):
                cookie = tokens[i]
        elif token in ("--data-raw", "--data", "--data-binary", "-d"):
            i += 1
            if i < len(tokens):
                data_raw = tokens[i]
        elif token.startswith("--data-raw="):
            data_raw = token.split("=", 1)[1]
        elif not token.startswith("-") and not url:
            url = token
        i += 1

    params = dict(parse_qsl(data_raw, keep_blank_values=True)) if data_raw else {}
    return {"url": url, "headers": headers, "cookie": cookie, "params": params}


def _endpoint_kind(url: str) -> str | None:
    low = (url or "").lower()
    if "allstockskulist" in low:
        return "total_stock"
    if "stockskulist" in low:
        return "warehouse_stock"
    if "listgoodsdocdetail" in low:
        return "stockin_detail"
    if "listgoodsdoc" in low:
        return "stockin"
    if "startexcelexport" in low or "validateexcelexport" in low:
        return "sales_export"
    if "tradeorderdetiallist" in low or "queryidlist" in low or "querylist" in low:
        return "sales_export"
    return None


@dataclass
class JkyWebAuth:
    """从 cURL 提取的网页登录态 + 已学习的端点 URL。"""

    authorization: str = ""
    cookie: str = ""
    ati: str = ""
    bx_v: str = ""
    user_agent: str = ""
    accept_language: str = ""
    common_verify: str = ""
    endpoint_urls: dict[str, str] = field(default_factory=dict)
    updated_at: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "authorization": self.authorization,
            "cookie": self.cookie,
            "ati": self.ati,
            "bx_v": self.bx_v,
            "user_agent": self.user_agent,
            "accept_language": self.accept_language,
            "common_verify": self.common_verify,
            "endpoint_urls": self.endpoint_urls,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "JkyWebAuth":
        return cls(
            authorization=data.get("authorization", ""),
            cookie=data.get("cookie", ""),
            ati=data.get("ati", ""),
            bx_v=data.get("bx_v", ""),
            user_agent=data.get("user_agent", ""),
            accept_language=data.get("accept_language", ""),
            common_verify=data.get("common_verify", ""),
            endpoint_urls=dict(data.get("endpoint_urls") or {}),
            updated_at=data.get("updated_at", ""),
        )


def auth_from_curl_text(raw: str) -> JkyWebAuth:
    """解析单条 cURL → 登录态。要求 authorization（或 access_token）与 cookie 齐全。"""
    info = parse_curl_text(raw)
    headers = info["headers"]
    params = info["params"]

    authorization = headers.get("authorization") or params.get("access_token", "")
    if not authorization:
        raise JkyWebSessionError(
            "cURL 中没有 authorization 头或 access_token 参数，请复制登录吉客云后的数据请求", "parse"
        )
    if not authorization.lower().startswith("bearer "):
        authorization = f"Bearer {authorization}"
    if not info.get("cookie"):
        raise JkyWebSessionError(
            "cURL 中没有 cookie，请复制包含 cookie 的浏览器请求（勿用无痕窗口复制）", "parse"
        )

    auth = JkyWebAuth(
        authorization=authorization,
        cookie=info["cookie"],
        ati=headers.get("ati", ""),
        bx_v=headers.get("bx-v", ""),
        user_agent=headers.get("user-agent", "Mozilla/5.0"),
        accept_language=headers.get("accept-language", "zh-CN,zh;q=0.9"),
        common_verify=params.get("commonVerify", ""),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    kind = _endpoint_kind(info["url"])
    if kind and info["url"].startswith("http"):
        auth.endpoint_urls[kind] = info["url"]
    return auth


def merge_curl_texts(raw: str) -> JkyWebAuth:
    """支持一次粘贴多条 cURL（空行分隔）：第一条提供登录态，其余用于学习端点 URL。"""
    chunks = [c for c in re.split(r"\n\s*\n", raw.strip()) if c.strip()]
    if not chunks:
        raise JkyWebSessionError("粘贴内容为空", "parse")
    primary: JkyWebAuth | None = None
    errors: list[str] = []
    for chunk in chunks:
        try:
            info = parse_curl_text(chunk)
        except JkyWebError as exc:
            errors.append(str(exc))
            continue
        if primary is None:
            if not info["headers"].get("authorization") and not info["params"].get("access_token"):
                errors.append("第一条 cURL 缺少 authorization，已跳过")
                continue
            primary = auth_from_curl_text(chunk)
        else:
            kind = _endpoint_kind(info["url"])
            if kind and info["url"].startswith("http"):
                primary.endpoint_urls.setdefault(kind, info["url"])
            if info["params"].get("commonVerify") and not primary.common_verify:
                primary.common_verify = info["params"]["commonVerify"]
    if primary is None:
        raise JkyWebSessionError("；".join(errors) or "没有可用的 cURL", "parse")
    return primary


# ---------------------------------------------------------------------------
# SessionManager：登录态落库（Fernet 加密）与状态机
# ---------------------------------------------------------------------------

class JkyWebSessionManager:
    def __init__(self, db: Session):
        self.db = db

    def _connection(self) -> IntegrationConnection:
        row = (
            self.db.query(IntegrationConnection)
            .filter_by(provider=PROVIDER, mode=CONNECTION_MODE)
            .first()
        )
        if row is None:
            row = IntegrationConnection(provider=PROVIDER, mode=CONNECTION_MODE, phase=1)
            self.db.add(row)
            self.db.flush()
        return row

    def _credential(self) -> IntegrationCredential:
        row = (
            self.db.query(IntegrationCredential)
            .filter_by(provider=PROVIDER)
            .order_by(IntegrationCredential.id.desc())
            .first()
        )
        if row is None:
            row = IntegrationCredential(
                provider=PROVIDER, key_hint="吉客云网页登录态", secret_encrypted=""
            )
            self.db.add(row)
            self.db.flush()
        return row

    def load_auth(self) -> JkyWebAuth:
        cred = self._credential()
        if not cred.secret_encrypted:
            raise JkyWebSessionError("吉客云网页登录态未配置，请先粘贴 cURL 更新登录状态", "unconfigured")
        try:
            data = json.loads(decrypt_secret(cred.secret_encrypted))
        except Exception as exc:  # noqa: BLE001
            raise JkyWebSessionError(
                f"登录态解密失败（APP_SECRET_KEY 变更？）：{exc}", "unconfigured"
            ) from exc
        return JkyWebAuth.from_json(data)

    def save_auth(self, auth: JkyWebAuth, status: str = SESSION_ACTIVE) -> IntegrationConnection:
        cred = self._credential()
        cred.secret_encrypted = encrypt_secret(json.dumps(auth.to_json(), ensure_ascii=False))
        conn = self._connection()
        conn.status = status
        if status == SESSION_ACTIVE:
            conn.last_success_at = datetime.now(timezone.utc)
            conn.error_summary = ""
        meta = dict(conn.meta or {})
        meta["sessionUpdatedAt"] = auth.updated_at
        meta["endpointUrls"] = auth.endpoint_urls
        conn.meta = meta
        self.db.flush()
        return conn

    def set_status(self, status: str, message: str = "") -> None:
        conn = self._connection()
        conn.status = status
        conn.error_summary = message[:500]
        conn.last_tested_at = datetime.now(timezone.utc)
        self.db.flush()

    def connection(self) -> IntegrationConnection | None:
        return (
            self.db.query(IntegrationConnection)
            .filter_by(provider=PROVIDER, mode=CONNECTION_MODE)
            .first()
        )

    def has_auth(self) -> bool:
        cred = (
            self.db.query(IntegrationCredential)
            .filter_by(provider=PROVIDER)
            .order_by(IntegrationCredential.id.desc())
            .first()
        )
        return bool(cred and cred.secret_encrypted)


# ---------------------------------------------------------------------------
# WebClient：签名 / 请求 / 重试 / 错误分类 / 分页 / 导出任务流程
# ---------------------------------------------------------------------------

def signed_params(
    params: dict[str, Any], authorization: str, sign_secret: str, exclude: set[str] | None = None
) -> dict[str, str]:
    """参考项目签名：非空项按 key 排序拼接 k+v，md5(secret+payload+secret) 大写。"""
    out = {k: "" if v is None else str(v) for k, v in params.items()}
    out["timestamp"] = str(int(time.time() * 1000))
    out["access_token"] = authorization
    out["appkey"] = WEB_APP_KEY
    out.pop("sign", None)
    excluded = exclude or set()
    sign_items = [(k, v) for k, v in out.items() if k not in excluded and v != ""]
    sign_items.sort(key=lambda item: item[0])
    payload = "".join(k + v for k, v in sign_items)
    out["sign"] = hashlib.md5((sign_secret + payload + sign_secret).encode("utf-8")).hexdigest().upper()
    return out


_NEED_LOGIN_PATTERNS = ("未登录", "登录失效", "重新登录", "token", "登录已过期", "请先登录", "not login")
_NEED_VERIFY_PATTERNS = ("commonverify", "验证码", "手机验证", "安全验证", "verify")


def classify_business_error(payload: dict[str, Any], http_status: int, text: str) -> JkyWebError | None:
    """把吉客云业务错误翻译成状态机可识别的异常；返回 None 表示不是错误。"""
    code = payload.get("code")
    ok_codes = (None, 0, 200, "0", "200")
    if http_status < 400 and code in ok_codes:
        return None
    msg = str(payload.get("msg") or payload.get("message") or text or "")[:500]
    low = msg.lower()
    if http_status in (401, 403):
        return JkyWebError(f"吉客云登录态失效（HTTP {http_status}）：{msg}", "need_login")
    if any(p in low for p in _NEED_VERIFY_PATTERNS):
        return JkyWebError(f"吉客云要求安全验证：{msg}", "need_user_verify")
    if any(p in low for p in _NEED_LOGIN_PATTERNS):
        return JkyWebError(f"吉客云登录态失效：{msg}", "need_login")
    if code not in ok_codes:
        return JkyWebError(f"吉客云接口业务失败 code={code}：{msg}", "api")
    return JkyWebError(f"吉客云接口 HTTP {http_status}：{msg}", "api")


class JkyWebClient:
    """吉客云网页接口客户端（只读）。"""

    RETRIES = 4

    def __init__(self, auth: JkyWebAuth):
        if not settings.JKY_WEB_SIGN_SECRET:
            raise JkyWebError(
                "JKY_WEB_SIGN_SECRET 未配置：请在 .env 中配置吉客云网页签名密钥后重启", "unconfigured"
            )
        self.auth = auth
        self.base_url = settings.JKY_WEB_BASE_URL.rstrip("/")
        self.sign_secret = settings.JKY_WEB_SIGN_SECRET
        self._session = requests.Session()

    # -- 基础请求 ---------------------------------------------------------

    def _headers(self, module_code: str, referer: str | None = None) -> dict[str, str]:
        headers = {
            "accept": "text/plain, */*; q=0.01",
            "accept-language": self.auth.accept_language,
            "authorization": self.auth.authorization,
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "module_code": module_code,
            "origin": self.base_url,
            "referer": referer or f"{self.base_url}/",
            "user-agent": self.auth.user_agent,
            "x-requested-with": "XMLHttpRequest",
        }
        if self.auth.ati:
            headers["ati"] = self.auth.ati
        if self.auth.bx_v:
            headers["bx-v"] = self.auth.bx_v
        if self.auth.cookie:
            headers["cookie"] = self.auth.cookie
        return headers

    def _endpoint(self, kind: str, default_key: str | None = None) -> str:
        url = self.auth.endpoint_urls.get(kind)
        if url:
            return url
        path = DEFAULT_ENDPOINTS.get(default_key or kind, "")
        if not path:
            raise JkyWebError(
                f"端点 {kind} 的 URL 未知：请在设置页粘贴该吉客云页面的 cURL 以学习地址", "unconfigured"
            )
        return f"{self.base_url}{path}"

    def _request(self, method: str, url: str, params: dict[str, Any], module_code: str,
                 referer: str | None = None, exclude_sign: set[str] | None = None) -> dict[str, Any]:
        body = signed_params(params, self.auth.authorization, self.sign_secret, exclude_sign)
        last_error: Exception | None = None
        for attempt in range(1, self.RETRIES + 1):
            try:
                if method == "GET":
                    response = self._session.get(
                        url, headers=self._headers(module_code, referer), params=body, timeout=90
                    )
                else:
                    response = self._session.post(
                        url, headers=self._headers(module_code, referer), data=urlencode(body), timeout=90
                    )
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise JkyWebError(
                        f"吉客云返回非 JSON（HTTP {response.status_code}）：{response.text[:300]}",
                        "api",
                    ) from exc
                business_error = classify_business_error(payload, response.status_code, response.text)
                if business_error:
                    # 登录态/验证类错误不重试，直接上抛触发状态机
                    if business_error.kind in ("need_login", "need_user_verify"):
                        raise business_error
                    last_error = business_error
                else:
                    return payload
            except (requests.RequestException, JkyWebError) as exc:
                if isinstance(exc, JkyWebError) and exc.kind in ("need_login", "need_user_verify"):
                    raise
                last_error = exc
            if attempt < self.RETRIES:
                time.sleep(min(2 ** (attempt - 1), 8))
        raise JkyWebError(f"请求重试耗尽：{url} → {last_error}", "network")

    def post_form(
        self, url: str, params: dict[str, Any], module_code: str,
        referer: str | None = None, exclude_sign: set[str] | None = None,
    ) -> dict[str, Any]:
        return self._request("POST", url, params, module_code, referer, exclude_sign)

    def get_json(
        self, url: str, params: dict[str, Any], module_code: str,
        referer: str | None = None, exclude_sign: set[str] | None = None,
    ) -> dict[str, Any]:
        return self._request("GET", url, params, module_code, referer, exclude_sign)

    # -- 行提取与分页 ------------------------------------------------------

    @staticmethod
    def find_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        """兼容 result.data / data / result / list / rows / records 多种包裹。"""
        if not isinstance(payload, dict):
            raise JkyWebError(f"响应结构异常：{str(payload)[:300]}", "parse")
        result = payload.get("result")
        if isinstance(result, dict) and "data" in result and result["data"] is None:
            return []  # 翻页超出后吉客云返回 data:null，视为空页
        candidates = []
        if isinstance(result, dict):
            candidates.extend(
                [result.get("data"), result.get("list"), result.get("rows"), result.get("records")]
            )
        elif isinstance(result, list):
            candidates.append(result)
        candidates.extend([payload.get("data"), payload.get("list"), payload.get("rows")])
        for candidate in candidates:
            if isinstance(candidate, list):
                return candidate
            if isinstance(candidate, dict):
                for key in ("data", "list", "rows", "records", "stockSkuList"):
                    rows = candidate.get(key)
                    if isinstance(rows, list):
                        return rows
        raise JkyWebError(f"响应中找不到行列表：{str(payload)[:300]}", "parse")

    def paged_rows(
        self, url: str, base_params: dict[str, Any], module_code: str,
        page_size: int | None = None, max_pages: int = 500,
    ) -> list[dict[str, Any]]:
        page_size = page_size or settings.JKY_WEB_PAGE_SIZE
        all_rows: list[dict[str, Any]] = []
        previous_keys: tuple[str, ...] | None = None
        for page in range(max_pages):
            params = dict(base_params)
            params["pageIndex"] = str(page)
            params["pageSize"] = str(page_size)
            payload = self.post_form(url, params, module_code)
            rows = self.find_rows(payload)
            keys = tuple(
                str(r.get("docId") or r.get("recId") or r.get("skuId") or r.get("id") or idx)
                for idx, r in enumerate(rows)
            )
            if rows and keys == previous_keys:
                raise JkyWebError(f"第 {page} 页与上一页重复，停止以避免死循环", "api")
            previous_keys = keys
            all_rows.extend(rows)
            if len(rows) < page_size:
                break
        else:
            raise JkyWebError(f"翻页超过最大页数 {max_pages}", "api")
        return all_rows

    # -- 各数据端点 --------------------------------------------------------

    def fetch_stockin_headers(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """采购入库主单：inouttypes=101 只取采购入库。"""
        cols = [
            "docId", "goodsdocNo", "outBillNo", "inOutDate", "inouttype", "inouttypeName",
            "warehouseId", "warehouseName", "billNo", "sourceBillNo", "companyName",
            "vendCustomerName", "logisticName", "logisticNo", "totalQuantity",
            "baseCostTotalAmount", "baseHasTaxTotalAmount", "baseTaxTotalAmount",
            "goodsdocRemark", "gmtCreate", "redStatus",
        ]
        params = {
            "inouttypes": "101",
            "inOrOut": "1",
            "redDoc": "1",
            "archived": "0",
            "serviceType": "goodsdoc.search",
            "inOutDateStart": start.strftime("%Y-%m-%d %H:%M:%S"),
            "inOutDateEnd": end.strftime("%Y-%m-%d %H:%M:%S"),
            "cols": json.dumps(cols, ensure_ascii=False, separators=(",", ":")),
        }
        url = self._endpoint("stockin", "stockin")
        rows = self.paged_rows(url, params, "stockIn_List")
        unique: dict[str, dict[str, Any]] = {}
        for row in rows:
            doc_id = row.get("docId")
            if doc_id is not None:
                unique[str(doc_id)] = row
        return list(unique.values())

    def fetch_stockin_details(self, doc_ids: list[str]) -> list[dict[str, Any]]:
        """采购入库明细：按主单 docId 逐单分页拉取。"""
        cols = [
            "recId", "headId", "goodsdocNo", "goodsId", "goodsNo", "goodsName", "skuId",
            "skuName", "skuBarcode", "brandId", "brandName", "cateId", "cateName",
            "orderNum", "unitName", "quantity", "baceCurrencyCostPrice",
            "baceCurrencyCostAmount", "baceCurrencyWithTaxPrice", "baceCurrencyWithTaxAmount",
            "baceCurrencyTaxAmount", "taxRate", "batchNo", "productionDate",
            "expirationDate", "shelfLife", "warehouseId",
        ]
        url = self._endpoint("stockin_detail", "stockin_detail")
        result: list[dict[str, Any]] = []
        for doc_id in doc_ids:
            params = {
                "docId": doc_id,
                "serviceType": "goodsdoc.detail.search",
                "sortField": "",
                "sortOrder": "",
                "archived": "0",
                "cols": json.dumps(cols, ensure_ascii=False, separators=(",", ":")),
                "isShowForSerial": "0",
                "jlinkId": "",
            }
            rows = self.paged_rows(url, params, "stockIn_List")
            for row in rows:
                row.setdefault("headId", doc_id)
            result.extend(rows)
        unique: dict[str, dict[str, Any]] = {}
        for row in result:
            rec_id = row.get("recId")
            if rec_id is not None:
                unique[str(rec_id)] = row
        return list(unique.values())

    def fetch_total_stock(self) -> list[dict[str, Any]]:
        cols = [
            "warehouseId", "warehouseName", "goodsId", "goodsNo", "goodsName", "skuId",
            "skuName", "skuBarcode", "brandName", "cateName", "unitName",
            "currentQuantity", "lockingQuantity", "canUseQuantity", "orderAbleQuantity",
            "yesterdayQuantity", "weekQuantity", "threedayQuantity", "totalSaleQuantity",
            "price1", "price6", "price7",
        ]
        params = {"cols": json.dumps(cols, ensure_ascii=False, separators=(",", ":"))}
        url = self._endpoint("total_stock", "total_stock")
        return self.paged_rows(url, params, "total_stock")

    def fetch_warehouse_stock(self) -> list[dict[str, Any]]:
        cols = [
            "warehouseId", "warehouseName", "goodsId", "goodsNo", "goodsName", "skuId",
            "skuName", "skuBarcode", "brandName", "cateName",
            "currentQuantity", "canUseQuantity", "lockingQuantity",
            "costPrice", "costValue", "inQuantitySum", "outQuantitySum",
            "yesterdayQuantity", "weekQuantity", "threedayQuantity",
            "purchasingQuantity", "allocateQuantity", "salesReturnQuantity",
            "lastStockInTime",
        ]
        params = {
            "cols": json.dumps(cols, ensure_ascii=False, separators=(",", ":")),
            "serviceType": "stock.search.v2",
        }
        url = self._endpoint("warehouse_stock", "warehouse_stock")
        return self.paged_rows(url, params, "branch_stock")

    # -- 销售导出任务流程（参考 销售单查询_web.py / 销售单明细账_web.py）------

    def _export_params(
        self, excel_type: str, type_name: str, en_names: list[str], show_names: list[str],
        filter_key: str, time_begin: datetime, time_end: datetime,
    ) -> dict[str, str]:
        condition = {
            "pageInfo": {"pageIndex": 0, "pageSize": 500000, "sortField": "", "sortOrder": ""},
            "cols": en_names,
            "plaintext": 1 if self.auth.common_verify else 0,
            "version": "2.0",
            filter_key: {
                "timeType": 0,
                "timeBegin": time_begin.strftime("%Y-%m-%d %H:%M:%S"),
                "timeEnd": time_end.strftime("%Y-%m-%d %H:%M:%S"),
            },
        }
        out = {
            "serverName": "oms/oms/excel",
            "excelType": excel_type,
            "headersJson": json.dumps(
                {"enName": en_names, "showName": show_names}, ensure_ascii=False, separators=(",", ":")
            ),
            "conditionJson": json.dumps(condition, ensure_ascii=False, separators=(",", ":")),
            "datasource": "",
            "isMerge": "true",
            "typeName": type_name,
            "multiSheet": "false",
            "exportTotal": "",
            "isSyn": "false",
        }
        if self.auth.common_verify:
            out["commonVerify"] = self.auth.common_verify
            out["plaintext"] = "true"
        return out

    def start_export_task(self, params: dict[str, str], module_code: str = "order_queryv2") -> str:
        """validate → start；返回导出任务 id。触发手机验证时上抛 NEED_USER_VERIFY。"""
        validate_url = f"{self.base_url}/jkyun/excel-service/manager/validateExcelExport"
        self.post_form(validate_url, params, module_code)

        start_url = f"{self.base_url}/jkyun/excel-service/manager/startExcelExport"
        start = self.post_form(start_url, params, module_code)
        result = start.get("result")
        task_id = result.get("data") if isinstance(result, dict) else None
        task_id = task_id or start.get("data") or start.get("result")
        if isinstance(task_id, dict) and task_id.get("verifyType"):
            raise JkyWebError(
                "吉客云导出触发手机验证：请先在吉客云页面正常完成验证后，"
                "复制验证后的请求 cURL 重新更新登录状态",
                "need_user_verify",
            )
        if not task_id or isinstance(task_id, (dict, list)):
            raise JkyWebError(f"startExcelExport 未返回任务 id：{str(start)[:300]}", "api")
        return str(task_id)

    def poll_export_task(self, task_id: str, timeout_seconds: int | None = None) -> tuple[str, str]:
        """轮询系统任务列表直到导出完成；返回 (下载URL, 附件名)。"""
        timeout = timeout_seconds or settings.JKY_WEB_EXPORT_TIMEOUT_SECONDS
        interval = settings.JKY_WEB_EXPORT_POLL_SECONDS
        url = f"{self.base_url}/jkyun/tms/taskmanage/sysTaskInfoList"
        deadline = time.time() + timeout
        while time.time() < deadline:
            now = str(int(time.time() * 1000))
            params = {"pageIndex": "0", "pageSize": "10", "timeStamp": now, "_": now}
            payload = self.get_json(
                url, params, "task_list", referer=f"{self.base_url}/system/taskList.html",
                exclude_sign={"_"},
            )
            result = payload.get("result")
            rows = result.get("data") if isinstance(result, dict) else None
            rows = rows or payload.get("data") or []
            for row in rows if isinstance(rows, list) else []:
                if str(row.get("taskId")) != str(task_id):
                    continue
                for item in row.get("attachmentList") or []:
                    download_url = item.get("attachmentUrl")
                    if download_url:
                        if download_url.startswith("http://"):
                            download_url = download_url.replace("http://", "https://", 1)
                        return download_url, item.get("attachmentName", "")
            time.sleep(interval)
        raise JkyWebError(f"等待导出任务 {task_id} 超时（{timeout}s）", "api")

    def download_export(self, download_url: str) -> bytes:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                response = self._session.get(
                    download_url,
                    headers={"referer": f"{self.base_url}/", "user-agent": self.auth.user_agent},
                    timeout=(30, 300),
                )
                response.raise_for_status()
                return response.content
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(attempt * 3)
        raise JkyWebError(f"下载导出文件失败：{last_error}", "network")
