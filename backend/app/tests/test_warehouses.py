from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.models.catalog import InventorySnapshot
from app.models.consumable import Consumable, ConsumableTransaction, InboundConsumableUsage
from app.models.consumable_purchase import ConsumableReceipt
from app.services import consumable_purchase_service as purchase_svc
from app.services import warehouse_purchase_view
from app.services import warehouse_receipt_service
from app.services import warehouse_service


def test_warehouse_can_be_renamed_without_changing_identity(db_session):
    code = f"FACTORY-{uuid4().hex[:8].upper()}"
    row = warehouse_service.create_warehouse(
        db_session,
        code=code,
        name="测试工厂仓",
        warehouse_type="factory",
        purpose="both",
        is_sellable=False,
    )
    warehouse_id = row.id

    updated = warehouse_service.update_warehouse(
        db_session,
        warehouse_id,
        name="测试工厂仓（新名称）",
    )

    assert updated.id == warehouse_id
    assert updated.name == "测试工厂仓（新名称）"
    assert updated.code == code


def test_warehouse_reference_resolves_code_or_name(db_session):
    warehouse = warehouse_service.create_warehouse(
        db_session,
        code=f"MATCH-{uuid4().hex[:8].upper()}",
        name="匹配测试仓",
    )

    assert warehouse_service.resolve_warehouse_reference(
        db_session, code=warehouse.code,
    ) == (warehouse.code, warehouse.name)
    assert warehouse_service.resolve_warehouse_reference(
        db_session, name=warehouse.name,
    ) == (warehouse.code, warehouse.name)
    assert warehouse_service.resolve_warehouse_reference(
        db_session, code="UNKNOWN-WAREHOUSE", name="外部仓",
    ) == ("UNKNOWN-WAREHOUSE", "外部仓")


def test_unused_warehouse_can_be_physically_deleted(db_session):
    warehouse = warehouse_service.create_warehouse(
        db_session,
        code=f"DELETE-{uuid4().hex[:8].upper()}",
        name="待删除仓库",
    )
    warehouse_id = warehouse.id

    warehouse_service.delete_warehouse(db_session, warehouse_id)

    assert db_session.get(type(warehouse), warehouse_id) is None


def test_referenced_warehouse_cannot_be_physically_deleted(db_session):
    warehouse = warehouse_service.create_warehouse(
        db_session,
        code=f"USED-{uuid4().hex[:8].upper()}",
        name="已使用仓库",
    )
    db_session.add(InventorySnapshot(
        sku_id=900001,
        warehouse_id=warehouse.id,
        quantity=Decimal("1"),
        snapshot_at=warehouse.created_at,
        source="pytest",
        raw={},
    ))
    db_session.flush()

    try:
        warehouse_service.delete_warehouse(db_session, warehouse.id)
    except ValueError as exc:
        assert "库存快照" in str(exc)
    else:
        raise AssertionError("referenced warehouse must not be physically deleted")

    assert db_session.get(type(warehouse), warehouse.id) is not None


def test_consumable_receipt_uses_selected_factory_warehouse(db_session):
    warehouse = warehouse_service.create_warehouse(
        db_session,
        code=f"FACTORY-{uuid4().hex[:8].upper()}",
        name="指定工厂仓",
        warehouse_type="factory",
        purpose="both",
        is_sellable=False,
    )
    material = Consumable(
        code=f"HC-{uuid4().hex[:8].upper()}",
        name="测试彩盒",
        unit="个",
        stock_qty=Decimal("0"),
        factory_qty=Decimal("0"),
        transit_qty=Decimal("0"),
        purchased_qty=Decimal("0"),
        used_qty=Decimal("0"),
        min_stock_qty=Decimal("0"),
        status="active",
        raw={},
    )
    db_session.add(material)
    db_session.flush()

    purchase = purchase_svc.create_purchase(
        db_session,
        request_key=str(uuid4()),
        supplier_name="测试包材供应商",
        ordered_on=date(2026, 9, 8),
        items=[{
            "consumable_id": material.id,
            "quantity": Decimal("100"),
            "unit_cost": Decimal("0.5"),
        }],
        actor="pytest",
    )
    line = purchase_svc.serialize_purchase(db_session, purchase, detail=True)["items"][0]
    receipt_key = str(uuid4())

    warehouse_receipt_service.receive_consumable_purchase(
        db_session,
        purchase.id,
        request_key=receipt_key,
        received_on=date(2026, 9, 9),
        warehouse_id=warehouse.id,
        items=[{"item_id": line["id"], "quantity": Decimal("40")}],
        actor="pytest",
    )

    db_session.refresh(material)
    receipt = db_session.query(ConsumableReceipt).filter_by(request_key=receipt_key).one()
    tx = db_session.query(ConsumableTransaction).filter_by(
        source_type="consumable_receipt",
        source_id=receipt.id,
        consumable_id=material.id,
    ).one()

    assert receipt.warehouse_id == warehouse.id
    assert receipt.location == "factory"
    assert tx.warehouse_id == warehouse.id
    assert material.factory_qty == Decimal("40")
    assert material.stock_qty == Decimal("0")

    detail = warehouse_purchase_view.serialize_purchase(db_session, purchase, detail=True)
    assert detail["receipts"][0]["warehouseId"] == warehouse.id
    assert detail["receipts"][0]["warehouseName"] == "指定工厂仓"


def test_consumable_receipt_rejects_goods_only_warehouse(db_session):
    warehouse = warehouse_service.create_warehouse(
        db_session,
        code=f"B2C-{uuid4().hex[:8].upper()}",
        name="仅正品B2C仓",
        warehouse_type="b2c",
        purpose="goods",
        is_sellable=True,
    )
    material = Consumable(
        code=f"HC-{uuid4().hex[:8].upper()}",
        name="测试耗材",
        unit="个",
        stock_qty=Decimal("0"),
        factory_qty=Decimal("0"),
        transit_qty=Decimal("0"),
        purchased_qty=Decimal("0"),
        used_qty=Decimal("0"),
        min_stock_qty=Decimal("0"),
        status="active",
        raw={},
    )
    db_session.add(material)
    db_session.flush()
    purchase = purchase_svc.create_purchase(
        db_session,
        request_key=str(uuid4()),
        supplier_name="测试供应商",
        ordered_on=date(2026, 9, 8),
        items=[{"consumable_id": material.id, "quantity": Decimal("10"), "unit_cost": Decimal("1")}],
        actor="pytest",
    )
    line_id = purchase_svc.serialize_purchase(db_session, purchase, detail=True)["items"][0]["id"]

    try:
        warehouse_receipt_service.receive_consumable_purchase(
            db_session,
            purchase.id,
            request_key=str(uuid4()),
            received_on=date(2026, 9, 9),
            warehouse_id=warehouse.id,
            items=[{"item_id": line_id, "quantity": Decimal("1")}],
            actor="pytest",
        )
    except ValueError as exc:
        assert "不允许存放耗材" in str(exc)
    else:
        raise AssertionError("goods-only warehouse must reject consumable receipts")


def test_normalize_legacy_consumable_state_moves_only_known_rows(monkeypatch, db_session):
    warehouse = warehouse_service.create_warehouse(
        db_session,
        code=f"FACTORY-{uuid4().hex[:8].upper()}",
        name="归一化测试仓",
        warehouse_type="factory",
        purpose="consumable",
        is_sellable=False,
    )
    # 让测试不受其他测试已创建的仓库影响，专门验证唯一耗材仓分支。
    monkeypatch.setattr(warehouse_service, "active_for_purpose", lambda _db, _purpose: [warehouse])
    material = Consumable(
        code=f"HC-{uuid4().hex[:8].upper()}",
        name="归一化测试耗材",
        stock_qty=Decimal("110"),
        factory_qty=Decimal("0"),
        purchased_qty=Decimal("10"),
        used_qty=Decimal("2"),
        transit_qty=Decimal("0"),
        status="active",
        raw={},
    )
    db_session.add(material)
    db_session.flush()
    purchase = purchase_svc.create_purchase(
        db_session,
        request_key=str(uuid4()),
        supplier_name="归一化测试供应商",
        ordered_on=date(2026, 9, 9),
        items=[{"consumable_id": material.id, "quantity": Decimal("10"), "unit_cost": Decimal("1")}],
        actor="pytest",
    )
    receipt = ConsumableReceipt(
        purchase_id=purchase.id,
        number=f"HR-LEGACY-{uuid4().hex[:8].upper()}",
        request_key=str(uuid4()),
        request_fingerprint="legacy",
        received_on=date(2026, 9, 9),
        location="own",
    )
    db_session.add(receipt)
    db_session.flush()
    known = ConsumableTransaction(
        consumable_id=material.id,
        transaction_type="purchase",
        quantity=Decimal("10"),
        warehouse_id=None,
        location="own",
        source_type="consumable_receipt",
        source_id=receipt.id,
        stock_before=Decimal("100"),
        stock_after=Decimal("110"),
        factory_before=Decimal("0"),
        factory_after=Decimal("0"),
        note="legacy",
    )
    usage = InboundConsumableUsage(
        link_id=9001,
        inbound_document_id=9001,
        consumable_id=material.id,
        quantity=Decimal("2"),
    )
    inbound = ConsumableTransaction(
        consumable_id=material.id,
        transaction_type="consume",
        quantity=Decimal("2"),
        warehouse_id=None,
        location=None,
        source_type="inbound_link",
        source_id=9001,
        stock_before=Decimal("110"),
        stock_after=Decimal("108"),
        factory_before=Decimal("0"),
        factory_after=Decimal("0"),
        note="legacy inbound",
    )
    orphan = ConsumableTransaction(
        consumable_id=material.id,
        transaction_type="purchase",
        quantity=Decimal("1"),
        warehouse_id=None,
        location="own",
        source_type="consumable_receipt",
        source_id=999999,
        note="orphan legacy",
    )
    db_session.add_all([known, usage, inbound, orphan])
    db_session.flush()

    result = warehouse_receipt_service.normalize_legacy_consumable_warehouse_state(db_session)

    db_session.refresh(material)
    db_session.refresh(receipt)
    db_session.refresh(known)
    db_session.refresh(inbound)
    db_session.refresh(orphan)
    assert result["warehouseId"] == warehouse.id
    assert receipt.warehouse_id == warehouse.id
    assert receipt.location == "factory"
    assert known.warehouse_id == warehouse.id
    assert known.location == "factory"
    assert inbound.warehouse_id == warehouse.id
    assert inbound.location == "factory"
    assert inbound.stock_before is None
    assert inbound.factory_before == Decimal("110")
    assert orphan.warehouse_id == warehouse.id
    assert orphan.location == "factory"
    assert material.stock_qty == Decimal("0")
    assert material.factory_qty == Decimal("110")
