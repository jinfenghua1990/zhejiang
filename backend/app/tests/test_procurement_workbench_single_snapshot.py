from app.services.procurement_workbench_service import _funnel_from_rows, _todos_from_rows


def test_funnel_and_todos_share_same_row_semantics_without_database():
    pending = {
        "orderNo": "PENDING-1",
        "amount": 100.0,
        "purchaseContentComplete": False,
        "allocations": [],
        "inbound": [],
        "invoice": [],
        "settlement": [],
    }
    done = {
        "orderNo": "DONE-1",
        "amount": 100.0,
        "purchaseContentComplete": True,
        "allocations": [{"skuId": 1, "amount": 100.0}],
        "inbound": [{"amount": 100.0}],
        "invoice": [{"amount": 100.0}],
        "invoiceStatus": "done",
        "invoiceOutstanding": 0.0,
        "invoicedAmount": 100.0,
        "paidAmount": 100.0,
        "paidOn1688": True,
        "settlement": [],
    }
    rows = [pending, done]

    funnel = _funnel_from_rows(rows)
    todos = _todos_from_rows(rows)

    assert funnel["total"] == 2
    assert funnel["steps"][0]["count"] == 2
    assert funnel["steps"][-1]["count"] == 1
    assert todos["total"] == 2
    assert todos["pending"] == 1
    assert next(item for item in todos["items"] if item["key"] == "content")["count"] == 1
