from app.services.procurement_workbench_service import _paid_amount


def test_paid_amount_uses_max_instead_of_double_counting_same_payment():
    row = {
        "amount": 100.0,
        "paidAmount": 100.0,
        "paidOn1688": True,
        "settlement": [
            {"paid": True, "paidAmount": 100.0},
        ],
    }

    assert _paid_amount(row, cap=100.0) == 100.0


def test_paid_amount_caps_overpayment_at_order_amount():
    row = {
        "amount": 100.0,
        "paidAmount": 0.0,
        "paidOn1688": False,
        "settlement": [
            {"paid": True, "paidAmount": 70.0},
            {"paid": True, "paidAmount": 50.0},
        ],
    }

    assert _paid_amount(row, cap=100.0) == 100.0
