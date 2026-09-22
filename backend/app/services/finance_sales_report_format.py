"""财务月报的字段注册、默认模板配置与 Excel 渲染。

从 finance_sales_report_service 拆出的纯配置/格式化部分：不依赖数据库，
供主服务组装报表数据后调用。避免循环导入（本模块不反向依赖主服务）。
"""
from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from app.config import settings
from app.models.sales import SalesOrder, SalesOrderItem
from app.services.sales_scope import deal_orders_condition
from app.utils.money import quantize

FIELD_REGISTRY: dict[str, dict[str, str]] = {
    "period": {"label": "月度时间", "type": "text"},
    "warehouse": {"label": "仓库", "type": "text"},
    "tax_code": {"label": "税务编号", "type": "text"},
    "total_quantity": {"label": "发货总数量", "type": "number"},
    "total_sales": {"label": "销售总金额", "type": "money"},
    "total_cost": {"label": "销售总成本", "type": "money"},
}

DEFAULT_FIELD_KEYS = [
    "period",
    "warehouse",
    "tax_code",
    "total_quantity",
    "total_sales",
    "total_cost",
]

DEFAULT_RULES: dict[str, Any] = {
    "valid_order_mode": "paid_or_completed",
    "refund_mode": "recorded_non_cancelled",
    "timezone": settings.TZ,
    "order_level_value_mode": "first_item_only",
}


def default_fields() -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "label": meta["label"],
            "enabled": key in DEFAULT_FIELD_KEYS,
        }
        for key, meta in FIELD_REGISTRY.items()
    ]


def _normalize_fields(value: Any) -> list[dict[str, Any]]:
    """只接收注册字段；保存数组顺序即导出顺序，同时自动补齐新版本新增字段。"""
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "").strip()
        if key not in FIELD_REGISTRY or key in seen:
            continue
        label = str(raw.get("label") or FIELD_REGISTRY[key]["label"]).strip()[:80]
        result.append({"key": key, "label": label or FIELD_REGISTRY[key]["label"],
                       "enabled": bool(raw.get("enabled", True))})
        seen.add(key)
    for key, meta in FIELD_REGISTRY.items():
        if key not in seen:
            result.append({"key": key, "label": meta["label"], "enabled": False})
    return result or default_fields()


def _normalize_emails(value: Any) -> list[str]:
    result: list[str] = []
    for raw in value if isinstance(value, list) else []:
        email = str(raw).strip()
        if email and email not in result:
            result.append(email[:320])
    return result


def _valid_order_condition():
    """财务月报口径 = 成交口径（sales_scope）：待发/已发/待确认收货/已完成全部计入。

    关闭/取消/作废/退货/退款/待审核/待付款单不计入；不再额外叠加「已付款/已完成」，
    避免把发货在途、待发货等成交单排除在收入表之外（2026-09-17 与用户确认）。
    """
    return deal_orders_condition()


def _money(value: Decimal | None) -> Decimal:
    return value if value is not None else Decimal("0")


def _order_paid_shares(
    paid: Decimal, rows: list[tuple[SalesOrderItem, Decimal | None]]
) -> list[Decimal]:
    """把订单客户实付金额按行成本（数量 × 入库加权成本）占比分摊到明细行。

    个别行缺入库成本时退化为按销售数量占比；行数据完全不可用时均分。
    """
    if not rows:
        return []
    weights = [
        _money(item.quantity) * unit_cost if unit_cost is not None else _money(item.quantity)
        for item, unit_cost in rows
    ]
    total = sum(weights, Decimal("0"))
    if total <= 0:
        return [paid / len(rows)] * len(rows)
    return [paid * (weight / total) for weight in weights]


def _string_decimal(value: Decimal | None) -> str:
    return str(quantize(value or Decimal("0"), Decimal("0.01")))


UNBILLED_KEY_SEPARATOR = "\x1f"


def unbilled_detail_key(detail: dict[str, Any]) -> str:
    """返回可持久化的明细键；无票收入明细已按税务编号 + 产品聚合。"""
    return f"{str(detail.get('taxCode') or '').strip()}{UNBILLED_KEY_SEPARATOR}{str(detail.get('product') or '').strip()}"


def _warehouse_of(order: SalesOrder) -> str:
    raw = order.raw or {}
    name = str(raw.get("warehouseName") or raw.get("warehouse") or "").strip()
    return name or "未标记仓库"


def _as_excel_value(key: str, raw: Any) -> Any:
    if raw in (None, ""):
        return ""
    field_type = FIELD_REGISTRY.get(key, {}).get("type")
    if field_type in {"money", "number"}:
        try:
            return float(raw)
        except (TypeError, ValueError):
            return raw
    return raw


def to_xlsx(report: dict[str, Any]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "销售汇总"
    fields = report.get("fields", [])
    ws.append([field["label"] for field in fields])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in report.get("rows", []):
        ws.append([_as_excel_value(field["key"], row.get(field["key"])) for field in fields])

    # 合计行：只对数值列累加，其余留空；仓库列显示「合计」。
    summary = report.get("summary", {})
    sum_by_key = {
        "total_quantity": summary.get("totalQuantity"),
        "total_sales": summary.get("salesAmount"),
        "total_cost": summary.get("costAmount"),
    }
    total_row = [""] * len(fields)
    for index, field in enumerate(fields):
        if field["key"] == "warehouse":
            total_row[index] = "合计"
        elif field["key"] in sum_by_key and sum_by_key[field["key"]] is not None:
            total_row[index] = float(sum_by_key[field["key"]])
    ws.append(total_row)
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)

    for idx, field in enumerate(fields, start=1):
        field_type = FIELD_REGISTRY.get(field["key"], {}).get("type")
        if field_type == "money":
            for row_idx in range(2, ws.max_row + 1):
                ws.cell(row=row_idx, column=idx).number_format = "0.00"
        max_len = max(
            len(str(ws.cell(row=r, column=idx).value or ""))
            for r in range(1, min(ws.max_row, 200) + 1)
        )
        ws.column_dimensions[get_column_letter(idx)].width = min(max(max_len + 2, 10), 28)
    ws.freeze_panes = "A2"

    output = BytesIO()
    wb.save(output)
    return output.getvalue()


def generated_filename(year: int, month: int) -> str:
    return f"销售汇总_{year}{month:02d}.xlsx"