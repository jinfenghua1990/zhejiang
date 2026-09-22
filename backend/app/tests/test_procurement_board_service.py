from app.services import procurement_board_service as board_service
from app.services import procurement_workbench_service as workbench_service
from app.models.ops import ExceptionRecord


def _row(**overrides):
    row = {
        "amount": 100.0,
        "purchaseContentComplete": True,
        "allocations": [{"skuId": 1}],
        "purchaseOrders": [{}],
        "inbound": [{"consumableUsageDecided": True}],
        "invoice": [{"amount": 100.0, "verified": False}],
        "invoiceStatus": "done",
        "consumable": {},
        "settlement": [],
        "paidOn1688": False,
    }
    row.update(overrides)
    return row


def test_board_uses_shared_pending_queue_and_respects_po_bypass():
    assert board_service._first_undone_label(_row(invoice=[])) == "invoice"
    assert board_service._first_undone_label(
        _row(purchaseOrders=[], jackyunPoBypassed=True)
    ) is None


def test_board_payment_counts_1688_fact_without_double_counting():
    row = _row(amount=100.0, paidAmount=100.0, paidOn1688=True)
    assert board_service._paid_amount(row, cap=100.0) == 100.0

    row["settlement"] = [{"paid": True, "paidAmount": 40.0}]
    assert board_service._paid_amount(row, cap=100.0) == 100.0


def test_board_flow_uses_same_purchase_and_invoice_facts():
    flow = board_service._flow_status(_row(purchaseOrders=[], jackyunPoBypassed=True))
    assert [item["done"] for item in flow] == [True, True, True, True, True]


def test_board_pending_queue_catches_partial_invoice():
    row = _row(
        invoice=[{"amount": 20.0}],
        invoiceStatus="partial",
        invoiceOutstanding=80.0,
        paidOn1688=True,
        paidAmount=100.0,
    )
    assert board_service._pending_queue(row) == "invoice"


def test_local_inbound_does_not_wait_for_jackyun_purchase_order():
    row = _row(
        purchaseOrders=[],
        jackyunPoBypassed=False,
        invoice=[],
        invoiceStatus="pending",
        invoiceOutstanding=100.0,
    )
    assert board_service._pending_queue(row) == "invoice"


def test_order_without_inbound_waits_for_inbound_directly():
    row = _row(purchaseOrders=[], inbound=[], invoice=[])
    assert board_service._pending_queue(row) == "inbound"


def test_exception_refs_do_not_collide_file_and_external_ids(db_session):
    """采购单异常不能误标同 ID 的 1688 文件订单。"""
    exception = ExceptionRecord(
        code="PURCHASE_PAYMENT_GAP",
        type="PURCHASE_PAYMENT_GAP",
        title="采购付款差异",
        status="pending",
        ref_table="external_purchase_orders",
        ref_id="193",
        detail={"poId": 193, "orderId": 193},
    )
    db_session.add(exception)
    db_session.flush()

    refs = workbench_service._exception_refs(db_session)
    # 返回 (文件订单异常映射, 采购单异常映射)，键为 ID、值为异常摘要列表
    assert refs == ({}, {193: [refs[1][193][0]]})
    assert refs[1][193][0]["title"] == "采购付款差异"
    assert not workbench_service._row_has_exception(
        {"orderId": 193, "externalPoId": 194}, refs
    )
    assert workbench_service._row_has_exception(
        {"orderId": 192, "externalPoId": 193}, refs
    )
    # 异常摘要跟随行返回，供前端直接展示报错原因
    assert workbench_service._row_exception_info({"orderId": 192, "externalPoId": 193}, refs) == refs[1][193]
    assert workbench_service._row_exception_info({"orderId": 193, "externalPoId": 194}, refs) == []
