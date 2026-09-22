from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.catalog import Warehouse
from app.models.consumable import Consumable, ConsumableTransaction
from app.models.consumable_purchase import ConsumablePurchaseItem
from app.services import consumable_purchase_service
from app.services.consumable_service import record_transaction


def _material(db_session, stock: str = "5") -> Consumable:
    row = Consumable(
        code=f"PYTEST-INTEGRITY-HC-{uuid4().hex[:10]}",
        name="耗材库存完整性测试",
        category="包装",
        unit="个",
        stock_qty=Decimal(stock),
        factory_qty=Decimal("0"),
        transit_qty=Decimal("0"),
        purchased_qty=Decimal(stock),
        used_qty=Decimal("0"),
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_send_factory_cannot_create_impossible_negative_own_stock(db_session):
    material = _material(db_session, "5")

    with pytest.raises(ValueError, match="自有仓库存不足"):
        record_transaction(
            db_session,
            consumable_id=material.id,
            transaction_type="send_factory",
            quantity="6",
            note="pytest over-dispatch",
        )

    db_session.refresh(material)
    assert material.stock_qty == Decimal("5.0000")
    assert material.transit_qty == Decimal("0.0000")


def test_loss_cannot_exceed_physical_own_stock(db_session):
    material = _material(db_session, "5")

    with pytest.raises(ValueError, match="自有仓库存不足"):
        record_transaction(
            db_session,
            consumable_id=material.id,
            transaction_type="loss",
            quantity="6",
            note="pytest over-loss",
        )

    db_session.refresh(material)
    assert material.stock_qty == Decimal("5.0000")


def test_conflicting_business_source_replay_is_rejected(db_session):
    material = _material(db_session, "0")

    first = record_transaction(
        db_session,
        consumable_id=material.id,
        transaction_type="purchase",
        quantity="1",
        source_type="pytest_source",
        source_id=9001,
        note="same source",
    )

    with pytest.raises(ValueError, match="同一业务来源已经写入不同"):
        record_transaction(
            db_session,
            consumable_id=material.id,
            transaction_type="purchase",
            quantity="2",
            source_type="pytest_source",
            source_id=9001,
            note="same source",
        )

    db_session.refresh(material)
    assert material.stock_qty == Decimal("1.0000")
    assert db_session.query(ConsumableTransaction).filter_by(
        source_type="pytest_source", source_id=9001, consumable_id=material.id
    ).count() == 1
    assert first.quantity == Decimal("1.0000")


def test_received_consumable_purchase_cannot_move_order_date_after_receipt(db_session):
    material = _material(db_session, "0")
    purchase = consumable_purchase_service.create_purchase(
        db_session,
        request_key=str(uuid4()),
        supplier_name="耗材完整性供应商",
        ordered_on=date(2026, 9, 1),
        items=[{
            "consumable_id": material.id,
            "quantity": Decimal("5"),
            "unit_cost": Decimal("2"),
        }],
        actor="pytest",
    )
    line = db_session.query(ConsumablePurchaseItem).filter_by(
        purchase_id=purchase.id
    ).one()
    consumable_purchase_service.receive_purchase(
        db_session,
        purchase.id,
        request_key=str(uuid4()),
        received_on=date(2026, 9, 2),
        items=[{"item_id": line.id, "quantity": Decimal("5")}],
        actor="pytest",
    )

    with pytest.raises(ValueError, match="采购日期不能晚于已登记收货日期"):
        consumable_purchase_service.update_purchase(
            db_session,
            purchase.id,
            ordered_on=date(2026, 9, 3),
        )

    db_session.refresh(purchase)
    assert purchase.ordered_on == date(2026, 9, 1)


def test_delete_received_purchase_reversal_keeps_original_warehouse(db_session):
    material = _material(db_session, "0")
    warehouse = Warehouse(
        code=f"PYTEST-INTEGRITY-WH-{uuid4().hex[:8]}",
        name="耗材完整性工厂仓",
        warehouse_type="factory",
        purpose="consumable",
        is_sellable=False,
        status="active",
    )
    db_session.add(warehouse)
    db_session.flush()

    purchase = consumable_purchase_service.create_purchase(
        db_session,
        request_key=str(uuid4()),
        supplier_name="耗材删除冲销供应商",
        ordered_on=date(2026, 9, 1),
        items=[{
            "consumable_id": material.id,
            "quantity": Decimal("5"),
            "unit_cost": Decimal("2"),
        }],
        actor="pytest",
    )
    line = db_session.query(ConsumablePurchaseItem).filter_by(
        purchase_id=purchase.id
    ).one()
    consumable_purchase_service.receive_purchase(
        db_session,
        purchase.id,
        request_key=str(uuid4()),
        received_on=date(2026, 9, 2),
        items=[{"item_id": line.id, "quantity": Decimal("5")}],
        location="factory",
        warehouse_id=warehouse.id,
        actor="pytest",
    )
    original = db_session.query(ConsumableTransaction).filter_by(
        consumable_id=material.id,
        transaction_type="purchase",
    ).one()

    consumable_purchase_service.delete_purchase(db_session, purchase.id)

    db_session.refresh(material)
    assert material.factory_qty == Decimal("0.0000")
    assert material.purchased_qty == Decimal("0.0000")
    assert db_session.get(ConsumableTransaction, original.id) is not None

    reversal = db_session.query(ConsumableTransaction).filter(
        ConsumableTransaction.consumable_id == material.id,
        ConsumableTransaction.transaction_type == "manual",
        ConsumableTransaction.source_type == "consumable_receipt_reversal",
    ).one()
    assert reversal.quantity == Decimal("-5.0000")
    assert reversal.source_id == original.id
    assert reversal.warehouse_id == warehouse.id
    assert reversal.location == "factory"


def test_factory_receive_source_replay_uses_normalized_factory_location(db_session):
    material = _material(db_session, "0")
    material.transit_qty = Decimal("5")
    db_session.flush()

    first = record_transaction(
        db_session,
        consumable_id=material.id,
        transaction_type="factory_receive",
        quantity="2",
        source_type="pytest_factory_receive",
        source_id=7001,
        note="factory receive replay",
    )
    second = record_transaction(
        db_session,
        consumable_id=material.id,
        transaction_type="factory_receive",
        quantity="2",
        source_type="pytest_factory_receive",
        source_id=7001,
        note="factory receive replay",
    )

    assert second.id == first.id
    db_session.refresh(material)
    assert material.transit_qty == Decimal("3.0000")
    assert material.factory_qty == Decimal("2.0000")
    assert first.location == "factory"


def test_edit_purchase_cost_does_not_rewrite_received_inventory_snapshot(db_session):
    material = _material(db_session, "0")
    purchase = consumable_purchase_service.create_purchase(
        db_session,
        request_key=str(uuid4()),
        supplier_name="耗材成本快照供应商",
        ordered_on=date(2026, 9, 1),
        items=[{
            "consumable_id": material.id,
            "quantity": Decimal("5"),
            "unit_cost": Decimal("2"),
        }],
        actor="pytest",
    )
    line = db_session.query(ConsumablePurchaseItem).filter_by(
        purchase_id=purchase.id
    ).one()
    consumable_purchase_service.receive_purchase(
        db_session,
        purchase.id,
        request_key=str(uuid4()),
        received_on=date(2026, 9, 2),
        items=[{"item_id": line.id, "quantity": Decimal("5")}],
        actor="pytest",
    )
    receipt_tx = db_session.query(ConsumableTransaction).filter_by(
        consumable_id=material.id,
        transaction_type="purchase",
    ).one()
    assert receipt_tx.unit_cost == Decimal("2.0000000000")

    consumable_purchase_service.update_purchase(
        db_session,
        purchase.id,
        items=[{
            "consumable_id": material.id,
            "quantity": Decimal("5"),
            "unit_cost": Decimal("3"),
        }],
    )

    db_session.refresh(line)
    db_session.refresh(receipt_tx)
    assert line.unit_cost == Decimal("3.0000000000")
    assert receipt_tx.unit_cost == Decimal("2.0000000000")
