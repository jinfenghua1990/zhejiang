"""到仓入库单视图聚合耗材采购入库流水（虚拟入库单）的测试。"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.models.alibaba1688_import import Alibaba1688Order
from app.models.catalog import Warehouse
from app.models.consumable import Consumable, ConsumableTransaction
from app.models.consumable_purchase import ConsumablePurchase, ConsumableReceipt
from app.services.inbound_document_view import list_inbound_documents


def _warehouse(db_session, suffix: str) -> Warehouse:
    row = Warehouse(
        code=f"PYTEST-WH-{suffix}-{uuid4().hex[:8]}",
        name=f"耗材入库视图测试仓 {suffix}",
        warehouse_type="factory",
        purpose="consumable",
        is_sellable=False,
        status="active",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _material(db_session, suffix: str) -> Consumable:
    row = Consumable(
        code=f"PYTEST-VIEW-HC-{suffix}-{uuid4().hex[:8]}",
        name=f"耗材入库视图测试 {suffix}",
        category="包装",
        unit="个",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _consumable_purchase(db_session, suffix: str, warehouse: Warehouse, order: Alibaba1688Order | None = None):
    purchase = ConsumablePurchase(
        number=f"PYTEST-HC-{suffix}-{uuid4().hex[:8]}",
        request_key=uuid4().hex,
        request_fingerprint=uuid4().hex,
        supplier_name=f"耗材入库视图测试供应商 {suffix}",
        ordered_on=datetime(2025, 4, 27).date(),
        source_order_id=order.id if order else None,
        status="received",
    )
    db_session.add(purchase)
    db_session.flush()
    receipt = ConsumableReceipt(
        purchase_id=purchase.id,
        number=f"PYTEST-HR-{suffix}-{uuid4().hex[:8]}",
        request_key=uuid4().hex,
        request_fingerprint=uuid4().hex,
        received_on=datetime(2025, 4, 28).date(),
        warehouse_id=warehouse.id,
        location="factory",
    )
    db_session.add(receipt)
    db_session.flush()
    return purchase, receipt


def _tx(db_session, suffix: str, material: Consumable, warehouse: Warehouse, *, tx_type: str,
        quantity: str, receipt: ConsumableReceipt | None = None, occurred_at: datetime | None = None):
    row = ConsumableTransaction(
        consumable_id=material.id,
        transaction_type=tx_type,
        quantity=Decimal(quantity),
        warehouse_id=warehouse.id,
        location="factory",
        source_type="consumable_receipt" if receipt else "manual",
        source_id=receipt.id if receipt else None,
        note=f"入库视图流水 {suffix}",
        occurred_at=occurred_at or datetime(2025, 4, 28, 10, 0, tzinfo=timezone.utc),
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_consumable_receipt_groups_into_inbound_documents(db_session):
    warehouse = _warehouse(db_session, "GROUP")
    material_a = _material(db_session, "GROUP-A")
    material_b = _material(db_session, "GROUP-B")
    order = Alibaba1688Order(external_order_id=f"PYTEST-VIEW-1688-{uuid4().hex}", import_id=0)
    db_session.add(order)
    db_session.flush()
    purchase, receipt = _consumable_purchase(db_session, "GROUP", warehouse, order)
    _tx(db_session, "GROUP-A", material_a, warehouse, tx_type="purchase", quantity="10", receipt=receipt)
    _tx(db_session, "GROUP-B", material_b, warehouse, tx_type="purchase", quantity="5", receipt=receipt)
    # 出库/调整流水不能被聚合成入库单。
    _tx(db_session, "GROUP-USE", material_a, warehouse, tx_type="consume", quantity="2")
    _tx(db_session, "GROUP-LOSS", material_b, warehouse, tx_type="loss", quantity="1")

    payload = list_inbound_documents(db_session, limit=500)

    consumable_rows = [row for row in payload["rows"] if row["source"] == "consumable"]
    assert len(consumable_rows) == 1, "同一次收货的两条 purchase 流水应合成一张单，consume/loss 不聚合"
    row = consumable_rows[0]
    assert row["inboundNo"] == receipt.number
    assert row["isLocal"] is True
    assert row["supplier"] == purchase.supplier_name
    assert row["warehouse"] == warehouse.name
    assert row["warehouseCode"] == warehouse.code
    assert row["status"] == "已入库"
    assert row["productCount"] == 2
    assert row["actualQuantity"] == 15.0
    assert row["arrivedQuantity"] == 15.0
    assert row["difference"] == 0.0
    assert row["inboundAt"] is not None and row["inboundAt"].startswith("2025-04-28T18:00:00+08:00")
    assert [item["sku"] for item in row["items"]] == [material_a.code, material_b.code]
    assert all(item["matchStatus"] == "matched" and item["difference"] == 0.0 for item in row["items"])
    assert row["refs"] == [{
        "orderId": order.id,
        "orderNo": order.external_order_id,
        "supplier": purchase.supplier_name,
        "platform": "1688",
        "linkId": row["id"],
        "matchMethod": "consumable_receipt",
        "confirmed": True,
        "note": f"耗材采购单 {purchase.number}",
    }]
    assert row["invoices"] == [] and row["invoiceCount"] == 0
    assert payload["stats"]["done"] >= 1
    assert warehouse.name in payload["warehouses"]


def test_manual_purchase_transaction_becomes_single_row(db_session):
    warehouse = _warehouse(db_session, "MANUAL")
    material = _material(db_session, "MANUAL")
    tx = _tx(db_session, "MANUAL", material, warehouse, tx_type="purchase", quantity="3")

    payload = list_inbound_documents(db_session, limit=500)

    consumable_rows = [row for row in payload["rows"] if row["source"] == "consumable"]
    assert len(consumable_rows) == 1
    row = consumable_rows[0]
    assert row["inboundNo"] == f"HC入库-{tx.id}"
    assert row["supplier"] == "未填写"
    assert row["refs"] == []
    assert row["items"][0]["actualQuantity"] == 3.0


def test_warehouse_filter_covers_consumable_rows(db_session):
    warehouse = _warehouse(db_session, "FILTER")
    other = _warehouse(db_session, "FILTER-OTHER")
    material = _material(db_session, "FILTER")
    _, receipt = _consumable_purchase(db_session, "FILTER", warehouse)
    _tx(db_session, "FILTER", material, warehouse, tx_type="purchase", quantity="4", receipt=receipt)

    matched = list_inbound_documents(db_session, warehouse=warehouse.name, limit=500)
    assert any(row["source"] == "consumable" and row["warehouse"] == warehouse.name for row in matched["rows"])

    by_code = list_inbound_documents(db_session, warehouse=warehouse.code, limit=500)
    assert any(row["source"] == "consumable" for row in by_code["rows"])

    excluded = list_inbound_documents(db_session, warehouse=other.name, limit=500)
    assert all(row["source"] != "consumable" for row in excluded["rows"])


def test_search_matches_consumable_sku_and_material_name(db_session):
    warehouse = _warehouse(db_session, "SEARCH")
    material = _material(db_session, "SEARCH")
    _, receipt = _consumable_purchase(db_session, "SEARCH", warehouse)
    _tx(db_session, "SEARCH", material, warehouse, tx_type="purchase", quantity="6", receipt=receipt)

    by_sku = list_inbound_documents(db_session, q=material.code, limit=500)
    assert any(row["source"] == "consumable" for row in by_sku["rows"])

    by_name = list_inbound_documents(db_session, q="耗材入库视图测试 SEARCH", limit=500)
    assert any(row["source"] == "consumable" for row in by_name["rows"])

    miss = list_inbound_documents(db_session, q="PYTEST-NO-SUCH-TERM", limit=500)
    assert all(row["source"] != "consumable" for row in miss["rows"])


def test_deleted_consumable_receipt_does_not_leave_ghost_inbound(db_session):
    warehouse = _warehouse(db_session, "DELETED")
    material = _material(db_session, "DELETED")
    purchase, receipt = _consumable_purchase(db_session, "DELETED", warehouse)
    tx = _tx(
        db_session, "DELETED", material, warehouse,
        tx_type="purchase", quantity="5", receipt=receipt,
    )
    db_session.flush()

    # 删除采购/收货时库存台账保留原 purchase 流水用于审计，但收货事实本身已撤销。
    # 与正式 delete_purchase 相同：先解除/删除子收货事实，再删除采购主单。
    db_session.delete(receipt)
    db_session.flush()
    db_session.delete(purchase)
    db_session.flush()

    payload = list_inbound_documents(db_session, limit=500)

    assert db_session.get(ConsumableTransaction, tx.id) is not None
    assert all(
        row["id"] != -tx.id
        for row in payload["rows"]
        if row["source"] == "consumable"
    )
