from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.catalog import ProductSku
from app.models.consumable import Consumable, ConsumableTransaction
from app.models.inventory_stocktake import InventoryStocktakeItem
from app.services import inventory_position_service
from app.services import inventory_stocktake_service as stocktake
from app.services import warehouse_service


def _goods_sku(db_session) -> ProductSku:
    suffix = uuid4().hex[:10].upper()
    row = ProductSku(
        product_id=None,
        jackyun_sku_id=f"PYTEST-{suffix}",
        sku_code=f"PD-{suffix}",
        product_type="single",
        sku_name="盘点测试正品",
        barcode=f"69{suffix}",
        unit="盒",
        status="active",
        raw={},
    )
    db_session.add(row)
    db_session.flush()
    return row


def _consumable(db_session) -> Consumable:
    suffix = uuid4().hex[:10].upper()
    row = Consumable(
        code=f"HC-{suffix}",
        name="盘点测试耗材",
        unit="个",
        stock_qty=Decimal("10"),
        factory_qty=Decimal("0"),
        transit_qty=Decimal("0"),
        purchased_qty=Decimal("10"),
        used_qty=Decimal("0"),
        min_stock_qty=Decimal("0"),
        status="active",
        raw={},
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_goods_stocktake_lifecycle_changes_inventory_only_after_confirm(db_session):
    warehouse = warehouse_service.create_warehouse(
        db_session,
        code=f"STK-{uuid4().hex[:8].upper()}",
        name="正品盘点测试仓",
        warehouse_type="b2c",
        purpose="goods",
        is_sellable=True,
    )
    sku = _goods_sku(db_session)

    task = stocktake.create_task(
        db_session,
        scope="partial",
        warehouse_id=warehouse.id,
        item_kinds=["goods"],
        selected_items=[{"kind": "goods", "id": sku.id}],
        actor="pytest",
    )
    detail = stocktake.task_detail(db_session, task.id)
    assert detail["status"] == "pending"
    assert detail["itemCount"] == 1
    assert detail["items"][0]["bookQty"] == "0.0000"

    before = inventory_position_service.current_positions(db_session)
    assert before["by_sku"].get(sku.id, Decimal("0")) == Decimal("0")

    item_id = detail["items"][0]["id"]
    stocktake.save_counts(
        db_session,
        task.id,
        [{"id": item_id, "actual_qty": "5", "reason": "首次实物盘点"}],
    )
    reviewed = stocktake.task_detail(db_session, task.id)
    assert reviewed["status"] == "review"
    assert reviewed["items"][0]["differenceQty"] == "5.0000"

    still_before_confirm = inventory_position_service.current_positions(db_session)
    assert still_before_confirm["by_sku"].get(sku.id, Decimal("0")) == Decimal("0")

    stocktake.confirm_task(db_session, task.id, actor="pytest")
    completed = stocktake.task_detail(db_session, task.id)
    assert completed["status"] == "completed"
    assert completed["confirmedBy"] == "pytest"

    after = inventory_position_service.current_positions(db_session)
    assert after["by_sku"][sku.id] == Decimal("5")
    assert after["by_sku_warehouse"][sku.id][warehouse.id] == Decimal("5")
    assert after["stocktake_adjustment_count"] >= 1


def test_stocktake_requires_reason_for_difference(db_session):
    warehouse = warehouse_service.create_warehouse(
        db_session,
        code=f"STK-{uuid4().hex[:8].upper()}",
        name="差异原因测试仓",
        warehouse_type="b2c",
        purpose="goods",
        is_sellable=True,
    )
    sku = _goods_sku(db_session)
    task = stocktake.create_task(
        db_session,
        scope="partial",
        warehouse_id=warehouse.id,
        item_kinds=["goods"],
        selected_items=[{"kind": "goods", "id": sku.id}],
        actor="pytest",
    )
    item = stocktake.task_detail(db_session, task.id)["items"][0]
    stocktake.save_counts(
        db_session,
        task.id,
        [{"id": item["id"], "actual_qty": "2", "reason": ""}],
    )

    with pytest.raises(ValueError, match="必须填写原因"):
        stocktake.confirm_task(db_session, task.id, actor="pytest")


def test_consumable_stocktake_writes_signed_stocktake_transaction(monkeypatch, db_session):
    warehouse = warehouse_service.create_warehouse(
        db_session,
        code=f"STK-{uuid4().hex[:8].upper()}",
        name="耗材盘点测试仓",
        warehouse_type="other",
        purpose="consumable",
        is_sellable=False,
    )
    material = _consumable(db_session)

    # 本用例只验证盘点闭环；同口径多仓安全拦截由独立用例覆盖。
    monkeypatch.setattr(stocktake, "_consumable_aggregate_location", lambda db, row: "own")

    task = stocktake.create_task(
        db_session,
        scope="partial",
        warehouse_id=warehouse.id,
        item_kinds=["consumable"],
        selected_items=[{"kind": "consumable", "id": material.id}],
        actor="pytest",
    )
    item = stocktake.task_detail(db_session, task.id)["items"][0]
    assert item["bookQty"] == "10.0000"

    stocktake.save_counts(
        db_session,
        task.id,
        [{"id": item["id"], "actual_qty": "8", "reason": "包装破损盘亏"}],
    )
    stocktake.confirm_task(db_session, task.id, actor="pytest")

    db_session.refresh(material)
    assert material.stock_qty == Decimal("8")
    tx = (
        db_session.query(ConsumableTransaction)
        .filter(
            ConsumableTransaction.source_type == "stocktake",
            ConsumableTransaction.source_id == item["id"],
            ConsumableTransaction.consumable_id == material.id,
        )
        .one()
    )
    assert tx.transaction_type == "stocktake"
    assert tx.quantity == Decimal("-2")
    assert tx.warehouse_id == warehouse.id


def test_consumable_stocktake_blocks_ambiguous_legacy_warehouse_bucket(db_session):
    first = warehouse_service.create_warehouse(
        db_session,
        code=f"STK-A-{uuid4().hex[:8].upper()}",
        name="耗材自有仓A",
        warehouse_type="other",
        purpose="consumable",
        is_sellable=False,
    )
    warehouse_service.create_warehouse(
        db_session,
        code=f"STK-B-{uuid4().hex[:8].upper()}",
        name="耗材自有仓B",
        warehouse_type="b2c",
        purpose="consumable",
        is_sellable=False,
    )
    _consumable(db_session)

    with pytest.raises(ValueError, match="无法安全拆分账面数"):
        stocktake.stocktake_candidates(
            db_session,
            warehouse_id=first.id,
            item_kinds=["consumable"],
        )


def test_cancelled_stocktake_never_affects_goods_inventory(db_session):
    warehouse = warehouse_service.create_warehouse(
        db_session,
        code=f"STK-{uuid4().hex[:8].upper()}",
        name="取消盘点测试仓",
        warehouse_type="b2c",
        purpose="goods",
        is_sellable=True,
    )
    sku = _goods_sku(db_session)
    task = stocktake.create_task(
        db_session,
        scope="partial",
        warehouse_id=warehouse.id,
        item_kinds=["goods"],
        selected_items=[{"kind": "goods", "id": sku.id}],
        actor="pytest",
    )
    item = db_session.query(InventoryStocktakeItem).filter_by(task_id=task.id).one()
    stocktake.save_counts(
        db_session,
        task.id,
        [{"id": item.id, "actual_qty": "7", "reason": "测试取消"}],
    )
    stocktake.cancel_task(db_session, task.id)

    positions = inventory_position_service.current_positions(db_session)
    assert positions["by_sku"].get(sku.id, Decimal("0")) == Decimal("0")
