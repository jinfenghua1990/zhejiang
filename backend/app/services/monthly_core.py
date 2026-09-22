"""月度经营核心口径。

本系统以自然月作为正式经营/财务时间单位。这里的函数只读取指定月份事实，
禁止把其他月份的订单、退款、应收或回款混入月结快照。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import settings
from app.models.payment import SettlementRecord
from app.models.sales import AftersalesOrder, SalesOrder
from app.services.reconciliation import settled_amounts_by_settlement
from app.services.sales_scope import deal_orders_condition
from app.utils.money import quantize, to_decimal


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{quantize(to_decimal(value), Decimal('0.01')):f}"


def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """返回业务时区自然月的 [start, end) 边界。"""
    if not (1 <= month <= 12):
        raise ValueError("非法月份")
    tz = ZoneInfo(settings.TZ)
    start = datetime(year, month, 1, tzinfo=tz)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=tz)
    else:
        end = datetime(year, month + 1, 1, tzinfo=tz)
    return start, end


def refund_total(db: Session, start: datetime, end: datetime) -> Decimal:
    """统一退款事实：按售后退款记录发生月统计。"""
    refunds = (
        db.query(AftersalesOrder)
        .filter(
            AftersalesOrder.type == "refund",
            AftersalesOrder.created_at_src >= start,
            AftersalesOrder.created_at_src < end,
        )
        .all()
    )
    return sum((to_decimal(row.refund_amount) for row in refunds), Decimal("0"))


def sales_overview(db: Session, year: int, month: int) -> dict[str, Any]:
    """指定月份销售/退款首屏指标；不读取其他月份。"""
    start, end = month_bounds(year, month)
    valid_sales = deal_orders_condition()
    period_filter = (
        SalesOrder.ordered_at >= start,
        SalesOrder.ordered_at < end,
        valid_sales,
    )
    orders = db.query(SalesOrder).filter(*period_filter).all()
    paid_orders = [row for row in orders if row.paid_amount is not None]
    sales_amount = sum((to_decimal(row.paid_amount) for row in paid_orders), Decimal("0"))

    refund_amount = refund_total(db, start, end)
    net_sales = sales_amount - refund_amount
    refund_rate = None
    if sales_amount > 0:
        refund_rate = f"{((refund_amount / sales_amount) * 100).quantize(Decimal('0.01'))}"

    return {
        "salesAmount": _money(sales_amount) if orders else None,
        "netSales": _money(net_sales) if orders else None,
        "orderCount": len(orders) if orders else None,
        "refundRate": refund_rate,
        # 毛利由 profit.compute(year, month) 写回，避免这里再维护第二套成本算法。
        "grossProfit": None,
        # 回款三个字段由 reconciliation_overview(year, month) 写回。
        "receivable": None,
        "received": None,
        "pendingReceive": None,
    }


def reconciliation_overview(db: Session, year: int, month: int) -> dict[str, Any]:
    """指定月份应收/已回款/待回款；SettlementRecord 自带 period_year/month。"""
    settlements = (
        db.query(SettlementRecord)
        .filter(
            SettlementRecord.period_year == year,
            SettlementRecord.period_month == month,
        )
        .all()
    )
    settled_amounts = settled_amounts_by_settlement(db, [row.id for row in settlements])
    receivable = Decimal("0")
    per_platform: dict[str, dict[str, Decimal]] = {}
    for row in settlements:
        expected = to_decimal(row.expected_amount)
        settled = settled_amounts.get(row.id, Decimal("0"))
        receivable += expected
        agg = per_platform.setdefault(
            row.platform,
            {"expected": Decimal("0"), "settled": Decimal("0")},
        )
        agg["expected"] += expected
        agg["settled"] += settled

    received = sum((value["settled"] for value in per_platform.values()), Decimal("0"))
    pending = max(receivable - received, Decimal("0"))
    overpaid = max(received - receivable, Decimal("0"))
    return {
        "receivable": f"{receivable:f}",
        "received": f"{received:f}",
        "pending": f"{pending:f}",
        "overpaid": f"{overpaid:f}",
        "byPlatform": {
            platform: {
                "expected": f"{value['expected']:f}",
                "settled": f"{value['settled']:f}",
            }
            for platform, value in per_platform.items()
        },
    }
