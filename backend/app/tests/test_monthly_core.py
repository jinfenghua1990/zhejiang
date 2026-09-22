from datetime import datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.config import settings
from app.models.payment import SettlementRecord
from app.models.sales import AftersalesOrder, SalesOrder
from app.services.monthly_core import reconciliation_overview, sales_overview


def _dt(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 12, 0, tzinfo=ZoneInfo(settings.TZ))


def test_sales_overview_is_strictly_month_scoped(db_session):
    token = uuid4().hex[:8]
    db_session.add_all([
        SalesOrder(
            order_no=f"MONTH-A-{token}",
            platform="taobao",
            order_status="已完成",
            paid_amount=Decimal("100.00"),
            ordered_at=_dt(2026, 8, 15),
        ),
        SalesOrder(
            order_no=f"MONTH-B-{token}",
            platform="taobao",
            order_status="已完成",
            paid_amount=Decimal("999.00"),
            ordered_at=_dt(2026, 9, 1),
        ),
        AftersalesOrder(
            aftersale_no=f"REFUND-A-{token}",
            order_no=f"MONTH-A-{token}",
            type="refund",
            status="done",
            refund_amount=Decimal("10.00"),
            created_at_src=_dt(2026, 8, 20),
        ),
        AftersalesOrder(
            aftersale_no=f"REFUND-B-{token}",
            order_no=f"MONTH-B-{token}",
            type="refund",
            status="done",
            refund_amount=Decimal("500.00"),
            created_at_src=_dt(2026, 9, 2),
        ),
    ])
    db_session.flush()

    august = sales_overview(db_session, 2026, 8)
    assert august["salesAmount"] == "100.00"
    assert august["netSales"] == "90.00"
    assert august["orderCount"] == 1
    assert august["refundRate"] == "10.00"


def test_reconciliation_overview_is_strictly_month_scoped(db_session):
    db_session.add_all([
        SettlementRecord(
            platform="taobao",
            period_year=2026,
            period_month=8,
            expected_amount=Decimal("120.00"),
            source="pytest",
            status="open",
        ),
        SettlementRecord(
            platform="pdd",
            period_year=2026,
            period_month=9,
            expected_amount=Decimal("880.00"),
            source="pytest",
            status="open",
        ),
    ])
    db_session.flush()

    august = reconciliation_overview(db_session, 2026, 8)
    assert august["receivable"] == "120.0000"
    assert august["received"] == "0"
    assert august["pending"] == "120.0000"
    assert set(august["byPlatform"]) == {"taobao"}
