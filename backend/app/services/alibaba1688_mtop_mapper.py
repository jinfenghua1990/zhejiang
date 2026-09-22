"""mtop.1688.trading.dataline.service 响应 → Alibaba1688Order 标准字段的防御式映射器。

真实 mtop 响应结构未知（需首捕采样回填），因此：
1. 所有字段用「候选路径列表」按序尝试，全部落空则该字段为空并记 warn；
2. 候选路径可被 ``settings.ALIBABA_1688_MTOP_FIELD_PATHS_JSON``（JSON 字符串）覆盖，
   首捕调试后免改码调整；
3. 未识别字段名保留在 ``_unmapped`` 键，随 raw_payload 落库，便于后续补映射；
4. 绝不在代码里臆造字段路径——初始候选仅为常见 mtop 结构惯例，首捕后修正。

路径语法：``a.b.0.c``（点分隔；数组下标用数字段），顶层从响应的 ``data`` 开始。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.config import settings

# 标准字段与 Excel 导入保持一致（见 adapters/alibaba1688_export_file.py FIELD_MAPPING）。
STANDARD_FIELDS = (
    "external_order_id", "buyer_company_name", "buyer_member_name",
    "seller_company_name", "seller_member_name",
    "goods_total", "freight", "discount", "actual_payment",
    "order_status", "order_time", "pay_time", "order_remark",
)

_TRACKING_KEYS = {
    "trackingNo", "logisticNo", "logisticsNo", "waybillNo",
    "mailNo", "mailno", "expressNo",
}
_CARRIER_KEYS = {
    "logisticsCompany", "logistics_company", "logisticName",
    "logistic_name", "carrier", "expressCompany", "expressName",
}

# 订单数组在响应内的候选容器路径（2026-09-04 首捕实测回填，见 data/alibaba1688-capture/）。
# 实测结构：data.data.result 是 JSON 编码字符串，解析后 data.data 才是订单数组；
# _walk 会自动穿透 JSON 字符串，因此路径写作 data.result.data.data。
CONTAINER_PATHS: list[str] = [
    "data.result.data.data",  # 实测 buyer-order-list 主接口
    "result.orderList",
    "result.orders",
    "result.list",
    "data.result.orderList",
    "data.result.orders",
    "data.orderList",
    "data.orders",
    "data.list",
]

# 每个标准字段在单个订单 dict 内的候选路径（首捕实测回填 + 保守猜测）。
ORDER_FIELD_PATHS: dict[str, list[str]] = {
    "external_order_id": [
        "idStr", "orderId", "orderNo", "id", "bizOrderId",
        "orderBaseInfo.orderId", "orderBaseInfo.id",
    ],
    "buyer_company_name": ["buyerCompany", "buyer.companyName", "buyerInfo.companyName"],
    "buyer_member_name": ["buyerMember", "buyer.loginId", "buyerNick", "buyerInfo.loginId"],
    "seller_company_name": [
        "sellerCompany", "supplierName", "seller.companyName",
        "sellerCompanyInfo.companyName", "sellerInfo.companyName",
    ],
    "seller_member_name": ["sellerMember", "seller.loginId", "sellerNick", "sellerInfo.loginId"],
    "goods_total": ["goodsTotal", "totalPrice", "sumProductPayment", "sumPayment", "orderAmount"],
    "freight": ["freight", "carriage", "postFee"],
    "discount": ["discount", "discountFee", "allPromotionFee"],
    "actual_payment": ["actualPayment", "payFee", "realPay", "totalAmount", "sumPayment"],
    "order_status": [
        "statusLabel", "statusText", "status", "orderStatus", "bizStatus",
        "orderBaseInfo.statusText", "orderBaseInfo.status",
    ],
    "order_time": ["orderTime", "createTime", "gmtCreate", "createdTime"],
    "pay_time": ["payTime", "paymentTime", "gmtPayTime", "paidTime", "gmtPayment"],
    "order_remark": [
        "orderRemark", "remark", "buyerRemark", "buyerMessage", "buyerMemo", "message",
        "orderBaseInfo.remark",
    ],
}


def _effective_paths() -> tuple[list[str], dict[str, list[str]]]:
    """读取候选路径；settings 覆盖优先（首捕调试后免改码）。"""
    containers = CONTAINER_PATHS
    fields = {k: list(v) for k, v in ORDER_FIELD_PATHS.items()}
    override = (settings.ALIBABA_1688_MTOP_FIELD_PATHS_JSON or "").strip()
    if override:
        try:
            parsed = json.loads(override)
            if isinstance(parsed.get("containers"), list):
                containers = [str(p) for p in parsed["containers"]]
            for key, paths in (parsed.get("fields") or {}).items():
                if key in fields and isinstance(paths, list):
                    fields[key] = [str(p) for p in paths]
        except (ValueError, TypeError):
            pass  # 覆盖配置非法时静默回退默认
    return containers, fields


def _walk(node: Any, path: str) -> tuple[bool, Any]:
    """按 ``a.b.0.c`` 路径取值；返回 (是否命中, 值)。

    中途遇到 JSON 编码的字符串会自动解析后继续穿透（实测 mtop 订单接口的
    data.data.result 就是嵌套 JSON 字符串）。
    """
    current = node
    for segment in path.split("."):
        if current is None:
            return False, None
        # JSON 字符串穿透：1688 实测把订单数组包在字符串里再包一层。
        if isinstance(current, str):
            try:
                current = json.loads(current)
            except (ValueError, TypeError):
                return False, None
        segment = segment.strip()
        if isinstance(current, dict):
            if segment in current:
                current = current[segment]
                continue
            return False, None
        if isinstance(current, (list, tuple)):
            try:
                current = current[int(segment)]
                continue
            except (ValueError, IndexError):
                return False, None
        return False, None
    return True, current


def extract_orders(raw_response: dict[str, Any]) -> list[dict[str, Any]]:
    """从单个 mtop 响应中提取订单数组（原始 dict，未映射）。"""
    containers, _ = _effective_paths()
    data_node = raw_response.get("data", raw_response)
    for path in containers:
        hit, value = _walk(data_node, path)
        if hit and isinstance(value, list) and value:
            return [item for item in value if isinstance(item, dict)]
    return []


def _first_hit(raw_order: dict[str, Any], candidates: list[str]) -> tuple[bool, Any]:
    for path in candidates:
        hit, value = _walk(raw_order, path)
        if hit and value not in (None, ""):
            return True, value
    return False, None


def _collect_named_values(node: Any, names: set[str]) -> list[str]:
    """递归收集订单明细中的物流字段；1688 将物流号放在 orderEntries 明细里。"""
    values: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in names and child not in (None, ""):
                    text = str(child).strip()
                    if text and text not in values:
                        values.append(text)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(node)
    return values


def _extract_logistics(raw_order: dict[str, Any]) -> dict[str, str]:
    """只保留 1688 响应中实际出现的物流事实。"""
    tracking = _collect_named_values(raw_order, _TRACKING_KEYS)
    carrier = _collect_named_values(raw_order, _CARRIER_KEYS)
    result: dict[str, str] = {}
    if tracking:
        result["trackingNo"] = "、".join(tracking)
    if carrier:
        result["company"] = "、".join(carrier)
    return result


def extract_order_id(raw_order: dict[str, Any]) -> str:
    """增量判重的快速路径：只解析订单号。"""
    _, fields = _effective_paths()
    hit, value = _first_hit(raw_order, fields["external_order_id"])
    if hit and value is not None:
        return str(value).strip()
    return ""


def map_order(raw_order: dict[str, Any]) -> dict[str, Any]:
    """映射为标准字段 dict；未识别字段名记录在 ``_unmapped``，原始 dict 存入 ``_raw``。"""
    _, fields = _effective_paths()
    result: dict[str, Any] = {}
    unmapped: list[str] = []
    for key in STANDARD_FIELDS:
        hit, value = _first_hit(raw_order, fields.get(key, []))
        if not hit:
            unmapped.append(key)
            continue
        if key in ("order_time", "pay_time"):
            result[key] = _mtop_to_datetime(value)
        elif key in ("goods_total", "freight", "discount", "actual_payment"):
            result[key] = _mtop_to_money(value)
        else:
            result[key] = str(value).strip()
    result["_unmapped"] = unmapped
    result["logistics"] = _extract_logistics(raw_order)
    result["_raw"] = raw_order
    return result


def _mtop_to_money(value: Any) -> Decimal | None:
    """1688 交易金额：实测 mtop 以「分」为单位返回纯整数（sumPayment/carriage 等，
    2026-09-04 首捕与 Excel 导入金额比对确认：750 分 = 7.50 元）。

    纯整数值按分转元；带小数点/千分位/货币符号的值视为元直接解析
    （兼容其他响应形态）。
    """
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return (Decimal(value) / 100).quantize(Decimal("0.01"))
    text = str(value).strip()
    if text and re.fullmatch(r"-?\d+", text):
        return (Decimal(text) / 100).quantize(Decimal("0.01"))
    return _mtop_to_decimal(value)


def mtop_to_datetime(value: Any) -> datetime | None:
    """公开别名（供服务层与测试使用）。"""
    return _mtop_to_datetime(value)


def _mtop_to_datetime(value: Any) -> datetime | None:
    """epoch 毫秒/秒、ISO 字符串、常见日期字符串容错。"""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except (ValueError, TypeError):
            return None
        # 13 位毫秒 / 10 位秒
        seconds = number / 1000 if number > 1e12 else number
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit() and len(text) in (10, 13):
        number = int(text)
        seconds = number / 1000 if len(text) == 13 else number
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    for pattern in (
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d%H%M%S", "%Y%m%d",
    ):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _mtop_to_decimal(value: Any) -> Decimal | None:
    """number / "1,234.56" / "¥1,234.56" 容错；无值返回 None。"""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    text = str(value).replace(",", "").replace("，", "").replace("¥", "").strip()
    if not text or text in {"-", "--", "/"}:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None
