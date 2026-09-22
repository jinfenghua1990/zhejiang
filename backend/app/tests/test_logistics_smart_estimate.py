from datetime import datetime
from decimal import Decimal

from app.models.logistics import LogisticsBill
from app.services.logistics_import_service import _regular_fee
from app.services.logistics_service import historical_models


def test_regular_quote_over_3kg_uses_weight_formula():
    quote = {
        "河北": [3.5, 3.8, 4.5, 6, "5+W*2"],
        "新疆": [16, 18, 32, 50, "15*W"],
    }
    assert _regular_fee("河北", Decimal("4"), quote) == Decimal("13.00")
    assert _regular_fee("新疆", Decimal("4"), quote) == Decimal("60.00")


def test_historical_logistics_model_learns_month_region_carrier_and_weight(db_session):
    bill = LogisticsBill(
        period_label="2026 H1",
        period_start=datetime(2026, 1, 1),
        period_end=datetime(2026, 6, 30),
        carrier="中通快递-常州",
        waybill_count=2,
        actual_amount=Decimal("8.40"),
        status="settled",
        raw={
            "format": "warehouse_logistics_bill_v1",
            "summary": {"directChargeAmount": "7.00"},
            "shipments": [
                {
                    "province": "浙江",
                    "carrier": "中通快递-常州",
                    "weightBand": "0-0.5kg",
                    "completedAt": "2026-06-10T10:00:00",
                    "fee": "3.50",
                },
                {
                    "province": "浙江",
                    "carrier": "中通快递-常州",
                    "weightBand": "0-0.5kg",
                    "completedAt": "2026-06-11T10:00:00",
                    "fee": "3.50",
                },
            ],
            "pickup": [],
        },
    )
    db_session.add(bill)
    db_session.flush()

    learned = historical_models(db_session)
    assert learned["summary"]["available"] is True
    assert learned["summary"]["sampleCount"] == 2
    assert learned["summary"]["averageFee"] == "4.20"
    assert learned["models"][0]["month"] == "2026-06"
    assert learned["models"][0]["province"] == "浙江"
    assert learned["models"][0]["avgFee"] == "4.20"
