import hashlib
import json
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.base import AdapterError, AdapterNotConfigured, AdapterPermissionError
from app.config import settings
from app.models.integration import RawApiPayload, SyncCheckpoint, SyncJob, SyncLog

"""JackyunAdapter —— 吉客云 MCP（Streamable HTTP）。

原则（规格 5）：
- 只接已订阅接口，不发明 API method
- 第一次请求保留 raw payload
- 分页 / 增量 / checkpoint / 幂等 upsert / 指数退避 / 限流器 / 调用日志
- 页面刷新不触发全量吉客云查询
"""

RATE_LIMIT_MIN_INTERVAL = 0.35  # 秒；保守限流，Phase 1 依据真实限流响应调整


class JackyunAdapter:
    provider = "jackyun"

    def __init__(self, db: Session):
        self.db = db
        self.url = settings.JACKYUN_MCP_URL
        self.app_key = settings.JACKYUN_APP_KEY
        self.token = settings.JACKYUN_MCP_TOKEN
        self.session_id: str | None = None
        self._last_call_ts: float = 0.0

    def ensure_configured(self) -> None:
        if not settings.jackyun_configured:
            raise AdapterNotConfigured("吉客云 MCP 未配置（JACKYUN_MCP_URL / JACKYUN_MCP_TOKEN 为空）")

    # ---------- MCP 传输层 ----------

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            # 吉客云 MCP 实测：Authorization 直接放裸 Token，不带 Bearer 前缀
            "Authorization": self.token,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _rate_limit(self) -> None:
        import time

        now = time.monotonic()
        wait = RATE_LIMIT_MIN_INTERVAL - (now - self._last_call_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_call_ts = time.monotonic()

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST JSON-RPC；记录 raw payload（首次方法）与调用日志。"""
        self._rate_limit()
        digest = self._request_digest(body)
        try:
            resp = httpx.post(self.url, json=body, headers=self._headers(), timeout=30)
        except httpx.HTTPError as exc:
            self._log("error", f"MCP 请求失败: {exc}")
            raise AdapterError(f"吉客云 MCP 网络错误: {exc}") from exc

        new_session = resp.headers.get("mcp-session-id")
        if new_session:
            self.session_id = new_session

        data: dict[str, Any] | None = None
        ctype = resp.headers.get("content-type", "")
        if resp.status_code == 200:
            if "text/event-stream" in ctype:
                data = self._parse_sse(resp.text)
            else:
                try:
                    data = resp.json()
                except ValueError:
                    data = {"_raw_text": resp.text[:2000]}
        else:
            self._log("error", f"MCP HTTP {resp.status_code}: {resp.text[:500]}")
            raise AdapterError(f"吉客云 MCP HTTP {resp.status_code}: {resp.text[:200]}")

        # raw payload 存档（幂等：同一 digest 只存首次）
        if data is not None:
            method_name = str(body.get("method", ""))
            if method_name == "tools/call":
                tool_name = (body.get("params") or {}).get("name")
                if tool_name:
                    method_name = str(tool_name)
            exists = (
                self.db.query(RawApiPayload)
                .filter(RawApiPayload.provider == self.provider, RawApiPayload.request_digest == digest)
                .first()
            )
            if not exists:
                self.db.add(
                    RawApiPayload(provider=self.provider, method=method_name,
                                  request_digest=digest, payload=data)
                )
                try:
                    self.db.commit()
                except IntegrityError:
                    # 并发首请求由另一任务已存档，本次无需把成功的业务调用变成失败。
                    self.db.rollback()
        return data or {}

    @staticmethod
    def _request_digest(body: dict[str, Any]) -> str:
        """JSON-RPC 的 id 每次随机，归档摘要只基于真实请求语义。"""
        stable_request = {"method": body.get("method"), "params": body.get("params", {})}
        return hashlib.sha256(
            json.dumps(stable_request, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:16]

    @staticmethod
    def _parse_sse(text: str) -> dict[str, Any]:
        """SSE 帧里取最后一条 JSON-RPC 响应。"""
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                try:
                    return json.loads(payload)
                except ValueError:
                    continue
        return {"_raw_sse": text[:2000]}

    def _rpc(self, method: str, params: dict[str, Any] | None = None, notify: bool = False) -> dict[str, Any] | None:
        body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "id": str(uuid.uuid4()),
        }
        if params is not None:
            body["params"] = params
        if notify:
            body.pop("id")
            self._post(body)
            return None
        result = self._post(body)
        if isinstance(result, dict) and "error" in result and result["error"]:
            err = result["error"]
            raise AdapterError(f"MCP error {err.get('code')}: {err.get('message')}")
        return result

    # ---------- 连接测试 ----------

    def test_connection(self) -> dict[str, Any]:
        """initialize → tools/list，返回已订阅工具名列表。真实失败如实抛出。"""
        self.ensure_configured()
        init = self._rpc("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "ecommerce-ops-platform", "version": "0.1.0"},
        })
        server_info = (init or {}).get("result", {}).get("serverInfo", {})
        self._rpc("notifications/initialized", {}, notify=True)
        tools_resp = self._rpc("tools/list", {})
        tools = [
            t.get("name", "")
            for t in ((tools_resp or {}).get("result", {}).get("tools", []))
            if isinstance(t, dict)
        ]
        return {"server_info": server_info, "tools": sorted(tools), "session_id": self.session_id}

    # ---------- 业务同步（Phase 1 依据真实字段 mapping 逐个实现） ----------

    # 订阅清单（规格 5）：API method ↔ MCP tool 名
    # tool 名来自 2026-09-01 真实 tools/list 响应（open-platform-mcp v1.0.0），非猜测
    SUBSCRIBED_METHODS = {
        "erp.storage.goodsdocin.v2": "getGoodsDocInListInfo",
        "erp.storage.goodsdocout.v2": "getGoodsDocOutListInfo",
        "erp.storage.goodslist": "getGoodsListInfoByGoodsNo",
        "erp-goods.pricelist.get": "getGoodsPriceListInfo",
        "erp.stockquantity.get": "getGoodsStockQuantityListInfo",
        "omsapi-business.order.get": "getOrderListInfo",
        "erp.purch.get": "getPurchOrderListInfo",
        "erp.purchreturn.get": "getPurchOrderReturnListInfo",
        "erp.purchordersett.get": "getPurchOrderSettleListInfo",
        "ass-business.returnchange.fullinfoget": "getReturnChangeListInfo",
        "wms.order.query-info.page": "getShopOrderLiseInfo",
        "oms.trade.fullinfoget": "getTradesListInfo",
        "erp.allocate.get": "getStockAllocateListInfo",
        "erp.warehouse.get": "getWarehouseListInfo",
    }

    def call_subscribed(self, method: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """调用已订阅工具。method 须为 API method 名或真实 tool 名，禁止发明。"""
        self.ensure_configured()
        tool = self.SUBSCRIBED_METHODS.get(method, method)
        if tool not in self.SUBSCRIBED_METHODS.values():
            raise AdapterError(f"拒绝调用未订阅/未确认的吉客云 method: {method}")
        resp = self._rpc("tools/call", {"name": tool, "arguments": arguments}) or {}
        result = resp.get("result", {})
        if isinstance(result, dict) and result.get("isError") is True:
            raise AdapterError(self._tool_error_message(result))
        return result

    # 以下同步方法：真实调用订阅工具 → raw payload 存档（_post 幂等）→ 按响应结构
    # 动态映射本地表。字段名一律以真实响应为准，不猜字段（规格 5/20）。
    # 未开通开放平台时工具调用会返回 subCode 0130000609，如实失败并进异常中心。

    def _fetch_and_upsert(self, method: str, arguments: dict[str, Any], *,
                          id_fields: tuple[str, ...], upsert,
                          max_pages: int = 100) -> dict[str, Any]:
        """通用同步骨架：调用 → 提取列表 → 幂等 upsert。

        id_fields: 真实响应中可作外部主键的字段名候选（按顺序取第一个存在者）
        upsert:    callable(db, record) -> bool，返回 True 表示新建
        """
        self.ensure_configured()
        base_arguments = dict(arguments)
        page_size = int(str(base_arguments.get("pageSize", "10")) or "10")
        page_index = int(str(base_arguments.get("pageIndex", "0")) or "0")
        paged = "pageSize" in base_arguments
        stats = {
            "fetched": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "pages": 0,
            "raw_stored": True,
        }
        previous_page_digest: str | None = None

        for _ in range(max_pages):
            page_arguments = dict(base_arguments)
            if paged:
                page_arguments["pageSize"] = str(page_size)
                page_arguments["pageIndex"] = str(page_index)

            resp = self.call_subscribed(method, page_arguments)
            items: list[dict[str, Any]] = []
            for data in self._tool_payloads(resp):
                self._raise_for_business_error(data)
                items.extend(self._extract_records(data))

            stats["pages"] += 1
            stats["fetched"] += len(items)
            page_digest = hashlib.sha256(
                json.dumps(items, ensure_ascii=False, sort_keys=True, default=str).encode()
            ).hexdigest()
            if items and page_digest == previous_page_digest:
                stats["stopped_reason"] = "repeated_page"
                break
            previous_page_digest = page_digest

            for rec in items:
                if not isinstance(rec, dict):
                    stats["skipped"] += 1
                    continue
                key = next((rec.get(f) for f in id_fields if rec.get(f) is not None), None)
                if key is None:
                    # 网店订单响应可能把主档包在 tradeOnline/shopOrder 内；只解
                    # 已知主档包装，避免把商品明细里的 id 当成主键。
                    for wrapper in ("tradeOnline", "shopOrder", "order"):
                        nested = rec.get(wrapper)
                        if isinstance(nested, dict):
                            key = next((nested.get(f) for f in id_fields if nested.get(f) is not None), None)
                            if key is not None:
                                break
                if key is None:
                    stats["skipped"] += 1
                    continue
                outcome = upsert(self.db, rec)
                if outcome is True:
                    stats["created"] += 1
                elif outcome is False:
                    stats["updated"] += 1
                else:
                    stats["skipped"] += 1

            if not paged or len(items) < page_size:
                break
            page_index += 1
        else:
            stats["stopped_reason"] = "page_limit"
        self.db.commit()
        return stats

    @classmethod
    def _tool_payloads(cls, response: dict[str, Any]) -> list[dict[str, Any]]:
        """解开吉客云 MCP 可能多层嵌套的 ``content.text`` JSON。

        同一工具在不同数据类型上会返回字典、数组，或再包一层 MCP content；此处只
        解传输包装，不猜业务字段。顶层数组统一包成 ``data``，供后续提取逻辑处理。
        """
        payloads: list[dict[str, Any]] = []

        def visit(value: Any) -> None:
            if isinstance(value, str):
                try:
                    visit(json.loads(value.strip()))
                except (ValueError, TypeError):
                    return
                return
            if isinstance(value, list):
                payloads.append({"data": value})
                return
            if not isinstance(value, dict):
                return
            if isinstance(value.get("content"), list):
                for block in value["content"]:
                    visit(block)
                return
            if value.get("type") == "text" and "text" in value:
                visit(value["text"])
                return
            payloads.append(value)

        visit(response)
        return payloads or [{"data": []}]

    @staticmethod
    def _tool_error_message(data: dict[str, Any]) -> str:
        message = str(data.get("msg") or data.get("message") or "吉客云业务接口调用失败")
        sub_code = str(data.get("subCode") or data.get("sub_code") or "").strip()
        return f"吉客云业务接口调用失败（{sub_code}）：{message}" if sub_code else message

    @classmethod
    def _raise_for_business_error(cls, data: dict[str, Any]) -> None:
        """识别 HTTP/MCP 成功响应里携带的吉客云业务失败。"""
        stack: list[Any] = [data]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                sub_code = str(current.get("subCode") or current.get("sub_code") or "").strip()
                if sub_code:
                    message = cls._tool_error_message(current)
                    if sub_code == "0130000609" or "未开通" in message or "权限" in message:
                        raise AdapterPermissionError(message)
                    raise AdapterError(message)
                if current.get("isError") is True:
                    raise AdapterError(cls._tool_error_message(current))
                code = current.get("code")
                has_message = "msg" in current or "message" in current
                if current.get("success") is False and has_message:
                    raise AdapterError(cls._tool_error_message(current))
                if has_message and code not in (None, 0, "0", "200", 200):
                    raise AdapterError(cls._tool_error_message(current))
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)
            elif isinstance(current, str) and current.lstrip().startswith(("{", "[")):
                try:
                    stack.append(json.loads(current))
                except ValueError:
                    pass

    @staticmethod
    def _extract_records(data: dict[str, Any]) -> list[dict[str, Any]]:
        """从真实响应中提取顶层业务记录列表，不把嵌套明细误当成主记录。"""
        collection_keys = (
            "data", "rows", "list", "records", "goods", "warehouseInfo",
            "goodsStockQuantity", "purchOrder", "purchOrderReturn", "returnChangeList",
            "stockAllocate", "trades", "tradeOnlineList", "shopOrder", "shopOrderList",
            "purchOrderSettle", "settlementList", "settleList",
        )

        def extract(value: Any) -> list[dict[str, Any]] | None:
            if isinstance(value, dict):
                result = value.get("result")
                if isinstance(result, dict):
                    result_records = extract(result)
                    if result_records is not None:
                        return result_records
                for key in collection_keys:
                    candidate = value.get(key)
                    if isinstance(candidate, list):
                        records = extract(candidate)
                        if records is not None:
                            return records
                return None
            if isinstance(value, list):
                if len(value) == 1 and isinstance(value[0], dict):
                    nested = extract(value[0])
                    if nested is not None:
                        return nested
                return [row for row in value if isinstance(row, dict)]
            return None

        return extract(data) or []

    @staticmethod
    def _decimal_or_none(value: Any) -> Decimal | None:
        if value is None or value == "":
            return None
        try:
            return Decimal(str(value))
        except Exception:
            return None

    @staticmethod
    def _source_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=ZoneInfo(settings.TZ))
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo(settings.TZ))
        except ValueError:
            pass
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, pattern).replace(tzinfo=ZoneInfo(settings.TZ))
            except ValueError:
                continue
        return None

    @staticmethod
    def _first_value(record: dict[str, Any], *fields: str) -> Any:
        """按真实响应中可能出现的别名取第一个非空值。"""
        for field in fields:
            value = record.get(field)
            if value is not None and value != "":
                return value
        return None

    @classmethod
    def _nested_record(cls, record: dict[str, Any], *fields: str) -> dict[str, Any]:
        """网店订单有时把主档包在 tradeOnline 内，统一解一层。"""
        for field in fields:
            value = record.get(field)
            if isinstance(value, dict):
                return value
        return record

    @staticmethod
    def _recent_window(hours: int) -> tuple[str, str]:
        """生成不超过接口上限的吉客云本地时区查询窗口。"""
        end = datetime.now(ZoneInfo(settings.TZ)).replace(microsecond=0)
        start = end - timedelta(hours=hours)
        return start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")

    def fetch_sales_order_rows(
        self, start: datetime, end: datetime, *, page_size: int = 10, max_pages: int = 100
    ) -> list[dict[str, Any]]:
        """只获取销售订单原始行，供三通道编排器统一规范化和写库。

        现有 ``sync_sales_orders`` 保留原行为；新的统一入口不能让 API 通道
        在 Provider 内直接写 ``sales_orders``，所以单独暴露 fetch-only 方法。
        """
        self.ensure_configured()
        base_arguments = {
            "pageSize": str(page_size),
            "pageIndex": "0",
            "fields": (
                "tradeNo,tradeStatus,goodsDetail.goodsNo,goodsDetail.goodsName,shopName,"
                "goodsDetail.sellTotal,sourceTradeNo,goodsDetail:sellCount,warehouseName,"
                "goodsDetail.sellPrice,mainPostid,goodsDetail.specName,goodsDetail.unit,"
                "payment,goodsDetail.barcode"
            ),
            "startConsignTime": start.strftime("%Y-%m-%d %H:%M:%S"),
            "endConsignTime": end.strftime("%Y-%m-%d %H:%M:%S"),
        }
        rows: list[dict[str, Any]] = []
        previous_digest: str | None = None
        for page_index in range(max_pages):
            arguments = {**base_arguments, "pageIndex": str(page_index)}
            response = self.call_subscribed("oms.trade.fullinfoget", arguments)
            page_rows: list[dict[str, Any]] = []
            for payload in self._tool_payloads(response):
                self._raise_for_business_error(payload)
                page_rows.extend(self._extract_records(payload))

            page_digest = hashlib.sha256(
                json.dumps(page_rows, ensure_ascii=False, sort_keys=True, default=str).encode()
            ).hexdigest()
            if page_rows and page_digest == previous_digest:
                raise AdapterError("吉客云销售订单分页重复，已停止以避免重复写库")
            previous_digest = page_digest
            rows.extend(page_rows)
            if len(page_rows) < page_size:
                return rows
        raise AdapterError(f"吉客云销售订单翻页超过最大页数 {max_pages}，拒绝静默截断")

    def sync_products(self) -> dict[str, Any]:
        from app.models.catalog import Product, ProductSku

        def _upsert(db: Session, rec: dict) -> bool:
            gid = str(rec.get("goodsNo") or rec.get("goods_id") or rec.get("goodsId") or rec.get("goodsno") or "").strip()
            if not gid:
                return None
            row = db.query(Product).filter_by(jackyun_goods_id=gid).first()
            product_created = row is None
            if row:
                # 品类被本地手工修改过（raw.categoryLocallyEdited）时，同步不再覆盖 category
                category_locked = bool((row.raw or {}).get("categoryLocallyEdited"))
                row.raw = {**rec, "categoryLocallyEdited": True} if category_locked else rec
                row.goods_code = str(rec.get("goodsNo") or rec.get("goods_code") or rec.get("goodsCode") or row.goods_code)
                row.goods_name = str(rec.get("goodsName") or rec.get("goods_name") or row.goods_name)
                if not category_locked:
                    row.category = str(rec.get("cateName") or row.category)
            else:
                row = Product(
                    jackyun_goods_id=gid,
                    goods_code=gid,
                    goods_name=str(rec.get("goodsName") or rec.get("goods_name") or ""),
                    category=str(rec.get("cateName") or ""),
                    raw=rec,
                )
                db.add(row)
                db.flush()

            barcode = str(rec.get("skuBarcode") or rec.get("barcode") or "").strip()
            # 无条码的默认规格货品也建 SKU：以货号作 SKU 键（goodsNo 全局唯一）
            sku_key = barcode or str(rec.get("goodsNo") or gid).strip()
            if not sku_key:
                return product_created
            # 吉客云常给 skuName 填"默认规格"占位——落库时回退为货品名，避免覆盖修复过的名称
            raw_sku_name = str(rec.get("skuName") or "").strip()
            sku_name = raw_sku_name if raw_sku_name and raw_sku_name != "默认规格" else row.goods_name
            sku = db.query(ProductSku).filter_by(jackyun_sku_id=sku_key).first()
            if sku:
                sku.product_id = row.id
                sku.sku_code = str(rec.get("goodsNo") or sku_key)
                sku.sku_name = sku_name or sku.sku_name
                sku.barcode = barcode
                sku.unit = str(rec.get("unitName") or sku.unit)
                sku.raw = rec
                return product_created
            db.add(ProductSku(
                product_id=row.id,
                jackyun_sku_id=sku_key,
                sku_code=str(rec.get("goodsNo") or sku_key),
                sku_name=sku_name,
                barcode=barcode,
                unit=str(rec.get("unitName") or ""),
                raw=rec,
            ))
            return True

        return self._fetch_and_upsert(
            "erp.storage.goodslist", {"pageSize": "10", "pageIndex": "0"},
            id_fields=("goodsNo", "goods_id", "goodsId", "goodsno"), upsert=_upsert
        )

    def sync_price_lists(self) -> dict[str, Any]:
        from app.models.catalog import Product, ProductSku

        def _upsert(db: Session, rec: dict) -> bool:
            sid = str(rec.get("skuBarcode") or rec.get("sku_id") or rec.get("skuId") or rec.get("skuid") or "").strip()
            if not sid:
                return None
            goods_no = str(rec.get("goodsNo") or rec.get("goods_code") or "").strip()
            product = None
            if goods_no:
                product = db.query(Product).filter_by(jackyun_goods_id=goods_no).first()
            row = db.query(ProductSku).filter_by(jackyun_sku_id=sid).first()
            sale_price = self._decimal_or_none(rec.get("price1"))
            if sale_price is None:
                sale_price = self._decimal_or_none(rec.get("minPrice"))
            if row:
                if product:
                    row.product_id = product.id
                row.raw = rec
                row.sku_code = goods_no or row.sku_code
                row.sku_name = str(rec.get("skuName") or row.sku_name)
                row.barcode = sid
                if sale_price is not None:
                    row.sale_price = sale_price
                return False
            db.add(ProductSku(
                product_id=product.id if product else None,
                jackyun_sku_id=sid,
                sku_code=goods_no or sid,
                sku_name=str(rec.get("skuName") or rec.get("sku_name") or ""),
                barcode=sid,
                sale_price=sale_price,
                raw=rec,
            ))
            return True

        return self._fetch_and_upsert(
            "erp-goods.pricelist.get", {
                "pageSize": "10", "pageIndex": "0",
                "cols": "skuBarcode,skuName,goodsNo,goodsName,currencyCode,currencyName,minPrice,price1,price2,price3,price4,price5",
            },
            id_fields=("skuBarcode", "sku_id", "skuId", "skuid"), upsert=_upsert
        )

    def sync_sales_orders(self) -> dict[str, Any]:
        from app.models.catalog import ProductSku
        from app.models.sales import SalesOrder, SalesOrderItem

        def _upsert(db: Session, rec: dict) -> bool:
            no = str(rec.get("tradeNo") or rec.get("trade_no") or rec.get("order_no") or rec.get("orderNo") or rec.get("tid") or "").strip()
            if not no:
                return None
            row = db.query(SalesOrder).filter_by(order_no=no).first()
            created = row is None
            if row:
                row.raw = rec
            else:
                row = SalesOrder(order_no=no)
                db.add(row)
                db.flush()
            row.platform = str(rec.get("shopName") or rec.get("platform") or "")
            row.order_type = "trade"
            row.order_status = str(rec.get("tradeStatus") or rec.get("order_status") or rec.get("status") or "")
            row.paid_amount = self._decimal_or_none(rec.get("payment"))
            row.raw = rec

            # 只有本次响应实际带回明细时才刷新明细，防止接口字段缺失时误删历史已落地项目。
            details = rec.get("goodsDetail")
            if isinstance(details, list):
                db.query(SalesOrderItem).filter_by(order_id=row.id).delete(synchronize_session=False)
                for detail in details:
                    if not isinstance(detail, dict):
                        continue
                    barcode = str(detail.get("barcode") or detail.get("skuBarcode") or "").strip()
                    sku_code = str(detail.get("goodsNo") or barcode)
                    sku = None
                    if barcode:
                        sku = db.query(ProductSku).filter_by(jackyun_sku_id=barcode).first()
                    if sku is None and sku_code:
                        sku = db.query(ProductSku).filter_by(sku_code=sku_code).first()
                    db.add(SalesOrderItem(
                        order_id=row.id,
                        sku_id=sku.id if sku else None,
                        sku_code=sku_code,
                        goods_name=str(detail.get("goodsName") or ""),
                        quantity=self._decimal_or_none(detail.get("sellCount")),
                        unit_price=self._decimal_or_none(detail.get("sellPrice")),
                        amount=self._decimal_or_none(detail.get("sellTotal")),
                        raw=detail,
                    ))
            return created

        start, end = self._recent_window(167)  # 接口单次最大 7 天，留 1 小时边界余量。
        return self._fetch_and_upsert(
            "oms.trade.fullinfoget", {
                "pageSize": "10", "pageIndex": "0",
                "fields": "tradeNo,tradeStatus,goodsDetail.goodsNo,goodsDetail.goodsName,shopName,goodsDetail.sellTotal,sourceTradeNo,goodsDetail:sellCount,warehouseName,goodsDetail.sellPrice,mainPostid,goodsDetail.specName,goodsDetail.unit,payment,goodsDetail.barcode",
                "startConsignTime": start, "endConsignTime": end,
            },
            id_fields=("tradeNo", "trade_no", "order_no", "orderNo", "tid"), upsert=_upsert
        )

    def sync_online_orders(self) -> dict[str, Any]:
        from app.models.catalog import ProductSku
        from app.models.sales import SalesOrder, SalesOrderItem

        def _upsert(db: Session, rec: dict) -> bool:
            source = self._nested_record(rec, "tradeOnline", "order")
            no = str(self._first_value(source, "tradeNo", "order_no", "orderNo", "tid")
                     or self._first_value(rec, "tradeNo", "order_no", "orderNo", "tid") or "").strip()
            if not no:
                return None
            row = db.query(SalesOrder).filter_by(order_no=no).first()
            created = row is None
            if row is None:
                row = SalesOrder(order_no=no, order_type="online")
                db.add(row)
                db.flush()
            row.platform = str(self._first_value(source, "shopName", "platform") or row.platform or "")
            row.order_type = row.order_type or "online"
            row.order_status = str(self._first_value(source, "orderStatus", "order_status", "status") or row.order_status or "")
            row.paid_amount = self._decimal_or_none(self._first_value(source, "payment", "paidAmount"))
            row.ordered_at = self._source_datetime(self._first_value(source, "createTime", "createdAt"))
            row.paid_at = self._source_datetime(self._first_value(source, "payTime", "paidAt"))
            row.raw = rec

            details = self._first_value(source, "goodsDetailList", "goodsDetail")
            if isinstance(details, list):
                db.query(SalesOrderItem).filter_by(order_id=row.id).delete(synchronize_session=False)
                for detail in details:
                    if not isinstance(detail, dict):
                        continue
                    barcode = str(self._first_value(detail, "goodsBarcode", "barcode", "skuBarcode") or "").strip()
                    sku_code = str(self._first_value(detail, "goodsNo", "skuCode") or barcode).strip()
                    sku = None
                    if barcode:
                        sku = db.query(ProductSku).filter_by(jackyun_sku_id=barcode).first()
                    if sku is None and sku_code:
                        sku = db.query(ProductSku).filter_by(sku_code=sku_code).first()
                    db.add(SalesOrderItem(
                        order_id=row.id,
                        sku_id=sku.id if sku else None,
                        sku_code=sku_code,
                        goods_name=str(self._first_value(detail, "goodsName", "skuName") or ""),
                        quantity=self._decimal_or_none(self._first_value(detail, "sellCount", "quantity")),
                        unit_price=self._decimal_or_none(self._first_value(detail, "price", "sellPrice", "unitPrice")),
                        amount=self._decimal_or_none(self._first_value(detail, "sellTotal", "amount")),
                        raw=detail,
                    ))
            return created

        start, end = self._recent_window(23)  # 该接口创建时间窗口不得超过 24 小时。
        return self._fetch_and_upsert(
            "omsapi-business.order.get", {
                "pageSize": "10", "pageIndex": "0",
                "startGmtCreate": start, "endGmtCreate": end,
            },
            id_fields=("tradeNo", "order_no", "orderNo", "tid"), upsert=_upsert
        )

    def sync_aftersales(self) -> dict[str, Any]:
        from app.models.sales import AftersalesOrder

        def _upsert(db: Session, rec: dict) -> bool:
            no = str(rec.get("returnChangeNo") or rec.get("return_id") or rec.get("aftersale_no")
                     or rec.get("refund_no") or rec.get("afterSaleId") or "").strip()
            if not no:
                return None
            row = db.query(AftersalesOrder).filter_by(aftersale_no=no).first()
            created = row is None
            details = rec.get("returnChangeGoodsDetail")
            amounts = [
                amount
                for item in details if isinstance(item, dict)
                for amount in [self._decimal_or_none(item.get("shouldReturnFee") or item.get("returnFee"))]
                if amount is not None
            ] if isinstance(details, list) else []
            refund_amount = sum(amounts, Decimal("0")) if amounts else None
            if row:
                row.raw = rec
            else:
                row = AftersalesOrder(aftersale_no=no)
                db.add(row)
            row.order_no = str(rec.get("sourceTradeNo") or rec.get("tradeNo") or rec.get("orderNo") or "")
            row.type = str(rec.get("type") or "return_change")
            row.status = str(rec.get("status") or "")
            row.refund_amount = refund_amount
            row.created_at_src = self._source_datetime(rec.get("gmtCreate"))
            row.raw = rec
            return created

        return self._fetch_and_upsert(
            "ass-business.returnchange.fullinfoget", {"pageSize": "10", "pageIndex": "0"},
            id_fields=("returnChangeNo", "return_id", "aftersale_no", "refund_no", "afterSaleId"), upsert=_upsert
        )

    def sync_inventory(self) -> dict[str, Any]:
        from app.models.catalog import InventorySnapshot, ProductSku, Warehouse
        from datetime import datetime, timezone

        snapshot_at = datetime.now(timezone.utc)

        def _upsert(db: Session, rec: dict) -> bool:
            sid = str(rec.get("skuBarcode") or rec.get("sku_id") or rec.get("skuId") or rec.get("skuid") or rec.get("goodsNo") or rec.get("goods_no") or "").strip()
            if not sid:
                return None
            sku = db.query(ProductSku).filter_by(jackyun_sku_id=sid).first()
            if not sku:
                sku = db.query(ProductSku).filter_by(sku_code=str(rec.get("goodsNo") or sid)).first()
            if not sku:
                return None
            qty = next((rec.get(key) for key in ("currentQuantity", "quantity", "stock", "qty") if rec.get(key) is not None), None)
            qty_d = self._decimal_or_none(qty)
            if qty_d is None:
                return None
            warehouse_code = str(rec.get("warehouseCode") or rec.get("warehouse_code") or "").strip()
            warehouse = db.query(Warehouse).filter_by(jackyun_warehouse_id=warehouse_code).first() if warehouse_code else None
            db.add(InventorySnapshot(
                sku_id=sku.id,
                warehouse_id=warehouse.id if warehouse else None,
                quantity=qty_d,
                snapshot_at=snapshot_at,
                source="jackyun",
                raw=rec,
            ))
            return True

        return self._fetch_and_upsert(
            "erp.stockquantity.get", {"pageSize": "10", "pageIndex": "0", "isChannelReserve": "1"},
            id_fields=("skuBarcode", "sku_id", "skuId", "skuid", "goodsNo", "goods_no"), upsert=_upsert
        )

    def sync_warehouses(self) -> dict[str, Any]:
        from app.models.catalog import Warehouse

        def _upsert(db: Session, rec: dict) -> bool:
            wid = str(rec.get("warehouseCode") or rec.get("warehouse_id") or rec.get("warehouseId") or rec.get("wms_no") or "").strip()
            if not wid:
                return None
            row = db.query(Warehouse).filter_by(jackyun_warehouse_id=wid).first()
            if row:
                row.raw = rec
                row.name = str(rec.get("warehouseName") or rec.get("warehouse_name") or rec.get("name") or row.name)
                return False
            db.add(Warehouse(
                jackyun_warehouse_id=wid,
                name=str(rec.get("warehouseName") or rec.get("warehouse_name") or rec.get("name") or ""),
                raw=rec,
            ))
            return True

        return self._fetch_and_upsert(
            "erp.warehouse.get", {"pageSize": "10", "pageIndex": "0"},
            id_fields=("warehouseCode", "warehouse_id", "warehouseId", "wms_no"), upsert=_upsert
        )

    def sync_purchase_orders(self) -> dict[str, Any]:
        from app.models.purchase import JackyunPurchaseOrder

        def _upsert(db: Session, rec: dict) -> bool:
            pid = str(rec.get("purchId") or rec.get("purch_id") or rec.get("id") or rec.get("purchNo") or "").strip()
            if not pid:
                return None
            row = db.query(JackyunPurchaseOrder).filter_by(jackyun_purch_id=pid).first()
            if row:
                row.raw = rec
                row.purch_no = str(rec.get("purchNo") or rec.get("purch_no") or row.purch_no)
                row.supplier_name = str(rec.get("vendName") or rec.get("supplierName") or rec.get("supplier_name") or row.supplier_name)
                amount = self._decimal_or_none(rec.get("totalAmount"))
                if amount is not None:
                    row.amount = amount
                row.status = str(rec.get("status") or row.status or "")
                return False
            db.add(JackyunPurchaseOrder(
                jackyun_purch_id=pid,
                purch_no=str(rec.get("purchNo") or rec.get("purch_no") or ""),
                supplier_name=str(rec.get("vendName") or rec.get("supplier_name") or rec.get("supplierName") or ""),
                amount=self._decimal_or_none(rec.get("totalAmount")),
                status=str(rec.get("status") or ""),
                raw=rec,
            ))
            return True

        return self._fetch_and_upsert(
            "erp.purch.get", {"pageSize": "10", "pageIndex": "0"},
            id_fields=("purchId", "purch_id", "id", "purchNo"), upsert=_upsert
        )

    def sync_purchase_settlements(self) -> dict[str, Any]:
        from app.models.jackyun import JackyunPurchaseSettlement

        def _upsert(db: Session, rec: dict) -> bool:
            no = str(self._first_value(rec, "settNo", "settle_id", "settleId", "id") or "").strip()
            if not no:
                return None
            row = db.query(JackyunPurchaseSettlement).filter_by(settlement_no=no).first()
            created = row is None
            if row is None:
                row = JackyunPurchaseSettlement(settlement_no=no)
                db.add(row)
            row.settlement_date = self._source_datetime(self._first_value(rec, "settDate", "settlementDate"))
            row.supplier_name = str(self._first_value(rec, "vendName", "supplierName", "supplier_name") or row.supplier_name or "")
            row.company_name = str(self._first_value(rec, "companyName", "company_name") or row.company_name or "")
            row.total_amount = self._decimal_or_none(self._first_value(rec, "totalAmount"))
            row.settlement_amount = self._decimal_or_none(self._first_value(rec, "settTotalAmount", "settlementAmount"))
            row.settlement_type = str(self._first_value(rec, "settType", "settlementType") or row.settlement_type or "")
            row.purchase_fee = self._decimal_or_none(self._first_value(rec, "purFee", "purchaseFee"))
            row.paid = self._decimal_or_none(self._first_value(rec, "paid"))
            row.status = str(self._first_value(rec, "status", "settStatus") or row.status or "")
            row.raw = rec
            return created

        return self._fetch_and_upsert(
            "erp.purchordersett.get", {
                "pageSize": "10", "pageIndex": "0",
                "cols": "settNo,settDate,vendName,companyName,totalAmount,settTotalAmount,settType,purFee,paid",
            },
            id_fields=("settNo", "settle_id", "settleId", "id"),
            upsert=_upsert,
        )

    def sync_purchase_returns(self) -> dict[str, Any]:
        from app.models.jackyun import JackyunPurchaseReturn

        def _upsert(db: Session, rec: dict) -> bool:
            no = str(self._first_value(rec, "orderNum", "return_id", "returnId", "id") or "").strip()
            if not no:
                return None
            row = db.query(JackyunPurchaseReturn).filter_by(return_no=no).first()
            created = row is None
            if row is None:
                row = JackyunPurchaseReturn(return_no=no)
                db.add(row)
            row.purchase_no = str(self._first_value(rec, "purchNo", "purchId", "purchOrderNo", "purchaseNo") or row.purchase_no or "")
            row.supplier_name = str(self._first_value(rec, "vendName", "supplierName", "supplier_name") or row.supplier_name or "")
            row.warehouse_code = str(self._first_value(rec, "warehouseCode", "warehouse_id", "warehouseId") or row.warehouse_code or "")
            row.warehouse_name = str(self._first_value(rec, "warehouseName", "warehouse_name") or row.warehouse_name or "")
            row.created_at_src = self._source_datetime(self._first_value(rec, "createTime", "createdAt", "createDate"))
            row.returned_at_src = self._source_datetime(self._first_value(rec, "returnTime", "returnedAt", "returnDate"))
            row.return_amount = self._decimal_or_none(self._first_value(rec, "returnAmount", "totalAmount", "amount"))
            row.status = str(self._first_value(rec, "status", "returnStatus") or row.status or "")
            row.raw = rec
            return created

        return self._fetch_and_upsert(
            "erp.purchreturn.get", {"pageSize": "10", "pageIndex": "0"},
            id_fields=("orderNum", "return_id", "returnId", "id"), upsert=_upsert,
        )

    def sync_inbound(self) -> dict[str, Any]:
        stats = self._sync_goods_documents("inbound", "erp.storage.goodsdocin.v2")
        return self._match_goods_document_items(stats)

    def sync_outbound(self) -> dict[str, Any]:
        stats = self._sync_goods_documents("outbound", "erp.storage.goodsdocout.v2")
        return self._match_goods_document_items(stats)

    def _match_goods_document_items(self, stats: dict[str, Any]) -> dict[str, Any]:
        """同步入库/出库单后立即刷新货品档案匹配；无明细时保持旧返回契约。"""
        from app.services.sku_matching_service import match_inbound_items

        match_stats = match_inbound_items(self.db, include_outbound=True)
        if match_stats.get("total", 0):
            return {**stats, "skuMatch": match_stats}
        return stats

    def _sync_goods_documents(self, document_type: str, method: str) -> dict[str, Any]:
        from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem

        def _upsert(db: Session, rec: dict) -> bool:
            no = str(self._first_value(rec, "goodsdocNo", "doc_id", "docId", "goodsdoc_no") or "").strip()
            if not no:
                return None
            row = (
                db.query(JackyunGoodsDocument)
                .filter_by(document_type=document_type, goodsdoc_no=no)
                .first()
            )
            created = row is None
            if row is None:
                row = JackyunGoodsDocument(document_type=document_type, goodsdoc_no=no)
                db.add(row)
                db.flush()
            row.document_at = self._source_datetime(self._first_value(rec, "inOutDate", "documentDate", "createTime"))
            row.warehouse_code = str(self._first_value(rec, "warehouseCode", "warehouse_id", "warehouseId") or row.warehouse_code or "")
            row.warehouse_name = str(self._first_value(rec, "warehouseName", "warehouse_name") or row.warehouse_name or "")
            row.company_name = str(self._first_value(rec, "companyName", "company_name") or row.company_name or "")
            # 供应商名称从 raw 提取，接口受限时保持为空，恢复后自动补齐
            row.supplier_name = str(self._first_value(rec, "vendName", "supplierName", "supplier_name", "supplier") or row.supplier_name or "")
            details = self._first_value(rec, "goodsDocDetailList", "goodsDetailList", "details")
            if isinstance(details, list):
                db.query(JackyunGoodsDocumentItem).filter_by(document_id=row.id).delete(synchronize_session=False)
                total = Decimal("0")
                has_quantity = False
                for line_no, detail in enumerate(details, start=1):
                    if not isinstance(detail, dict):
                        continue
                    quantity = self._decimal_or_none(self._first_value(detail, "quantity", "qty"))
                    if quantity is not None:
                        total += quantity
                        has_quantity = True
                    db.add(JackyunGoodsDocumentItem(
                        document_id=row.id,
                        line_no=line_no,
                        goods_no=str(self._first_value(detail, "goodsNo", "goodsCode") or ""),
                        sku_barcode=str(self._first_value(detail, "skuBarcode", "barcode") or ""),
                        goods_name=str(self._first_value(detail, "goodsName", "skuName") or ""),
                        quantity=quantity,
                        unit_name=str(self._first_value(detail, "unitName", "unit") or ""),
                        raw=detail,
                    ))
                row.total_quantity = total if has_quantity else None
            row.raw = rec
            return created

        return self._fetch_and_upsert(
            method, {
                "pageSize": "10", "pageIndex": "0",
                "selelctFields": "goodsdocNo,inOutDate,warehouseCode,warehouseName,vendName,supplierName,companyName,goodsDocDetailList.goodsNo,goodsDocDetailList.goodsName,goodsDocDetailList.quantity,goodsDocDetailList.skuBarcode,goodsDocDetailList.unitName",
            },
            id_fields=("goodsdocNo", "doc_id", "docId", "goodsdoc_no"), upsert=_upsert,
        )

    def sync_stock_allocations(self) -> dict[str, Any]:
        from app.models.jackyun import JackyunStockAllocation

        def _upsert(db: Session, rec: dict) -> bool:
            no = str(self._first_value(rec, "allocateNo", "allocate_no", "allocateId", "id") or "").strip()
            if not no:
                return None
            row = db.query(JackyunStockAllocation).filter_by(allocate_no=no).first()
            created = row is None
            if row is None:
                row = JackyunStockAllocation(allocate_no=no)
                db.add(row)
            row.created_at_src = self._source_datetime(self._first_value(rec, "createTime", "createdAt", "allocateTime"))
            row.out_warehouse_code = str(self._first_value(rec, "outWarehouseCode", "out_warehouse_code") or row.out_warehouse_code or "")
            row.out_warehouse_name = str(self._first_value(rec, "outWarehouseName", "out_warehouse_name") or row.out_warehouse_name or "")
            row.in_warehouse_code = str(self._first_value(rec, "inWarehouseCode", "in_warehouse_code") or row.in_warehouse_code or "")
            row.in_warehouse_name = str(self._first_value(rec, "inWarehouseName", "in_warehouse_name") or row.in_warehouse_name or "")
            row.status = str(self._first_value(rec, "status", "allocateStatus") or row.status or "")
            row.raw = rec
            return created

        return self._fetch_and_upsert(
            "erp.allocate.get", {"pageSize": "10", "pageIndex": "0"},
            id_fields=("allocateNo", "allocate_no", "allocateId", "id"), upsert=_upsert,
        )

    def sync_shop_orders(self) -> dict[str, Any]:
        from app.models.jackyun import JackyunShopOrder, JackyunShopOrderItem

        def _upsert(db: Session, rec: dict) -> bool:
            source = self._nested_record(rec, "tradeOnline", "shopOrder", "order")
            no = str(self._first_value(source, "tradeNo", "orderNo", "order_no", "tradeId")
                     or self._first_value(rec, "tradeNo", "orderNo", "order_no", "tradeId") or "").strip()
            if not no:
                return None
            row = db.query(JackyunShopOrder).filter_by(shop_order_no=no).first()
            created = row is None
            if row is None:
                row = JackyunShopOrder(shop_order_no=no)
                db.add(row)
                db.flush()
            row.source_trade_no = str(self._first_value(source, "sourceTradeNo", "source_trade_no") or row.source_trade_no or "")
            row.shop_name = str(self._first_value(source, "shopName", "platform") or row.shop_name or "")
            row.logistic_no = str(self._first_value(source, "logisticNo", "logisticsNo", "trackingNo") or row.logistic_no or "")
            row.api_type = str(self._first_value(source, "apiType") or row.api_type or "")
            row.created_at_src = self._source_datetime(self._first_value(source, "createTime", "createdAt"))
            row.paid_at_src = self._source_datetime(self._first_value(source, "payTime", "paidAt"))
            row.goods_count = self._decimal_or_none(self._first_value(source, "goodsCount", "quantity"))
            row.payment = self._decimal_or_none(self._first_value(source, "payment", "paidAmount"))
            row.status = str(self._first_value(source, "status", "orderStatus") or row.status or "")
            details = self._first_value(source, "goodsDetailList", "tradeOnlineGoodsList", "goodsDetail")
            if isinstance(details, list):
                db.query(JackyunShopOrderItem).filter_by(order_id=row.id).delete(synchronize_session=False)
                for line_no, detail in enumerate(details, start=1):
                    if not isinstance(detail, dict):
                        continue
                    db.add(JackyunShopOrderItem(
                        order_id=row.id,
                        line_no=line_no,
                        plat_goods_id=str(self._first_value(detail, "platGoodsId", "plat_goods_id") or ""),
                        goods_barcode=str(self._first_value(detail, "goodsBarcode", "barcode", "skuBarcode") or ""),
                        goods_name=str(self._first_value(detail, "goodsName", "skuName") or ""),
                        quantity=self._decimal_or_none(self._first_value(detail, "sellCount", "quantity")),
                        unit_price=self._decimal_or_none(self._first_value(detail, "price", "unitPrice", "sellPrice")),
                        amount=self._decimal_or_none(self._first_value(detail, "sellTotal", "amount")),
                        raw=detail,
                    ))
            row.raw = rec
            return created

        start, end = self._recent_window(23)
        return self._fetch_and_upsert(
            "wms.order.query-info.page", {
                "pageSize": "10", "pageIndex": "0",
                "fields": "tradeOnline.tradeNo,tradeOnline.logisticNo,tradeOnline.shopName,tradeOnline.payment,tradeOnline.payTime,tradeOnline.createTime,tradeOnline.goodsCount,tradeOnlineGoodsList.goodsBarcode,tradeOnlineGoodsList.goodsName,tradeOnlineGoodsList.platGoodsId,tradeOnlineGoodsList.sellCount,tradeOnlineGoodsList.price,tradeOnlineGoodsList.sellTotal,tradeOnline.tradeId,tradeOnline.apiType,tradeOnlineGoodsList.tradeId",
                "createTimeBegin": start, "createTimeEnd": end,
            },
            id_fields=("tradeNo", "orderNo", "order_no", "tradeId"), upsert=_upsert,
        )

    # ---------- 日志辅助 ----------

    def _log(self, level: str, message: str, job_id: int | None = None, data: dict | None = None) -> None:
        self.db.add(SyncLog(provider=self.provider, level=level, message=message,
                            sync_job_id=job_id, data=data or {}))
        self.db.commit()


def start_sync_job(db: Session, provider: str, job_type: str) -> SyncJob:
    job = SyncJob(provider=provider, job_type=job_type, status="running")
    from datetime import datetime, timezone

    job.started_at = datetime.now(timezone.utc)
    db.add(job)
    db.commit()
    return job


def finish_sync_job(db: Session, job: SyncJob, status: str, stats: dict | None = None, error: str = "") -> None:
    from datetime import datetime, timezone

    job.status = status
    job.finished_at = datetime.now(timezone.utc)
    job.stats = stats or {}
    job.error_summary = error
    db.commit()


def load_checkpoint(db: Session, provider: str, job_type: str) -> dict:
    row = db.query(SyncCheckpoint).filter_by(provider=provider, job_type=job_type).first()
    return row.checkpoint if row else {}


def save_checkpoint(db: Session, provider: str, job_type: str, checkpoint: dict) -> None:
    row = db.query(SyncCheckpoint).filter_by(provider=provider, job_type=job_type).first()
    if row:
        row.checkpoint = checkpoint
    else:
        db.add(SyncCheckpoint(provider=provider, job_type=job_type, checkpoint=checkpoint))
    db.commit()
