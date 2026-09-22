from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.catalog import Warehouse
from app.models.consumable import Consumable, ConsumableTransaction, InboundConsumableUsage
from app.models.consumable_purchase import ConsumablePurchase, ConsumableReceipt
from app.services import consumable_purchase_service as purchase_svc
from app.services import warehouse_service
from app.utils.money import to_decimal


def backfill_legacy_consumable_warehouse_links(db: Session) -> dict[str, object]:
    """把历史耗材收货/采购入库流水补到唯一的启用工厂仓。

    只有存在且仅存在一个可存放耗材的启用工厂仓时才执行，避免用猜测把历史
    数量写入错误仓库。只补仓库外键，不改数量、位置或历史流水内容。
    """
    warehouses = (
        db.query(Warehouse)
        .filter(
            Warehouse.status == "active",
            Warehouse.warehouse_type == "factory",
            Warehouse.purpose.in_(("consumable", "both")),
        )
        .order_by(Warehouse.id)
        .all()
    )
    if len(warehouses) != 1:
        return {
            "updatedReceipts": 0,
            "updatedTransactions": 0,
            "warehouseId": None,
            "reason": "启用的耗材工厂仓不是唯一，未自动回填",
        }
    warehouse = warehouses[0]
    receipts = db.query(ConsumableReceipt).filter(ConsumableReceipt.warehouse_id.is_(None)).all()
    receipt_ids = [receipt.id for receipt in receipts]
    for receipt in receipts:
        receipt.warehouse_id = warehouse.id
    updated_transactions = 0
    if receipt_ids:
        updated_transactions = db.query(ConsumableTransaction).filter(
            ConsumableTransaction.source_type == "consumable_receipt",
            ConsumableTransaction.source_id.in_(receipt_ids),
            ConsumableTransaction.warehouse_id.is_(None),
        ).update({"warehouse_id": warehouse.id}, synchronize_session=False)
    db.commit()
    return {
        "updatedReceipts": len(receipts),
        "updatedTransactions": int(updated_transactions),
        "warehouseId": warehouse.id,
        "warehouseName": warehouse.name,
    }


def normalize_legacy_consumable_warehouse_state(db: Session) -> dict[str, object]:
    """把旧双库存口径迁移到唯一的启用耗材仓。

    旧版本只保存了 ``own`` 或空仓库外键，导致收货单、入库关联扣减和耗材
    台账的仓库事实彼此不一致。只有当当前启用的耗材仓恰好唯一且为工厂仓时，
    才能安全把历史余额归到该仓；否则只返回原因，不猜仓、不改数量。

    迁移只改变仓库归属和对应的历史快照字段，不改变采购量、使用量或可用总量。
    找不到来源收货单的孤立流水也会保留；在唯一耗材仓约束下只补当前仓库归属，
    不补写或伪造它缺失的来源收货单。
    """
    warehouses = warehouse_service.active_for_purpose(db, "consumable")
    if len(warehouses) != 1 or warehouses[0].warehouse_type != "factory":
        return {
            "updatedReceipts": 0,
            "updatedTransactions": 0,
            "updatedUsageTransactions": 0,
            "movedMaterials": 0,
            "movedQuantity": "0",
            "warehouseId": None,
            "reason": "启用的耗材仓不是唯一工厂仓，未自动归一化",
        }

    warehouse = warehouses[0]
    receipts = db.query(ConsumableReceipt).order_by(ConsumableReceipt.id).all()
    receipt_by_id = {receipt.id: receipt for receipt in receipts}
    warehouse_ids = {receipt.warehouse_id for receipt in receipts if receipt.warehouse_id is not None}
    warehouse_ids.update(
        transaction.warehouse_id
        for transaction in db.query(ConsumableTransaction).all()
        if transaction.warehouse_id is not None
    )
    warehouse_by_id = {
        row.id: row
        for row in db.query(Warehouse).filter(Warehouse.id.in_(warehouse_ids)).all()
    } if warehouse_ids else {}

    updated_receipts = 0
    for receipt in receipts:
        if receipt.warehouse_id is None:
            receipt.warehouse_id = warehouse.id
            updated_receipts += 1
        resolved = warehouse_by_id.get(receipt.warehouse_id) or (
            warehouse if receipt.warehouse_id == warehouse.id else None
        )
        if resolved is not None:
            location = warehouse_service.legacy_location(resolved)
            if receipt.location != location:
                receipt.location = location
                updated_receipts += 1

    usage_keys = {
        (usage.link_id, usage.consumable_id)
        for usage in db.query(InboundConsumableUsage).all()
    }
    updated_transactions = 0
    updated_usage_transactions = 0
    transactions = db.query(ConsumableTransaction).order_by(ConsumableTransaction.id).all()
    for transaction in transactions:
        target = None
        if transaction.source_type == "consumable_receipt":
            receipt = receipt_by_id.get(transaction.source_id)
            if receipt is not None and receipt.warehouse_id is not None:
                target = warehouse_by_id.get(receipt.warehouse_id) or (
                    warehouse if receipt.warehouse_id == warehouse.id else None
                )
            elif transaction.warehouse_id is None:
                # 收货单可能因历史删除而不存在，但这类流水仍属于耗材收货台账；
                # 当前唯一耗材仓足以确定仓库归属，来源缺失本身继续留在审计流水中。
                target = warehouse
        elif transaction.source_type == "inbound_link" and (
            transaction.source_id,
            transaction.consumable_id,
        ) in usage_keys:
            # 入库单关联的耗材使用始终从配置的耗材仓扣减，不能跟随正品入库仓。
            target = warehouse
        elif transaction.warehouse_id is None and transaction.source_type in {
            "manual", "stocktake", "adjustment"
        }:
            # 这几类无来源流水是旧版耗材台账的期初/调整记录；当前只有一个
            # 耗材仓，归一到该仓不会改变其数量。
            target = warehouse

        if target is None or target.purpose not in {"consumable", "both"}:
            continue

        changed = False
        if transaction.warehouse_id is None:
            transaction.warehouse_id = target.id
            changed = True
        expected_location = warehouse_service.legacy_location(target)
        if transaction.location != expected_location:
            # 旧流水的 stock_before/after 实际记录的是唯一库存余额；迁移后
            # 把它们转到 factory_before/after，避免详情页继续显示“自有仓”。
            if (
                target.warehouse_type == "factory"
                and to_decimal(transaction.factory_before) == 0
                and transaction.stock_before is not None
            ):
                transaction.factory_before = transaction.stock_before
                transaction.factory_after = transaction.stock_after
                transaction.stock_before = None
                transaction.stock_after = None
            transaction.location = expected_location
            changed = True
        if changed:
            updated_transactions += 1
            if transaction.source_type == "inbound_link":
                updated_usage_transactions += 1

    moved_materials = 0
    moved_quantity = Decimal("0")
    for material in db.query(Consumable).order_by(Consumable.id).all():
        stock = to_decimal(material.stock_qty)
        if stock == 0:
            continue
        material.factory_qty = to_decimal(material.factory_qty) + stock
        material.stock_qty = Decimal("0")
        moved_materials += 1
        moved_quantity += stock

    db.commit()
    return {
        "updatedReceipts": updated_receipts,
        "updatedTransactions": updated_transactions,
        "updatedUsageTransactions": updated_usage_transactions,
        "movedMaterials": moved_materials,
        "movedQuantity": str(moved_quantity),
        "warehouseId": warehouse.id,
        "warehouseName": warehouse.name,
    }


def receive_consumable_purchase(
    db: Session,
    purchase_id: int,
    *,
    request_key: str,
    received_on: date,
    items: list[dict],
    warehouse_id: int | None = None,
    note: str = "",
    actor: str = "",
) -> ConsumablePurchase:
    """按仓库主档登记耗材收货，同时兼容旧 own/factory 库存口径。

    新操作必须落 warehouse_id；如果前端未传（旧客户端），仅在启用耗材仓唯一时
    自动使用该仓，避免旧入口把收货写进不确定的仓库。
    """
    warehouse = (
        warehouse_service.get_active(db, warehouse_id)
        if warehouse_id is not None
        else warehouse_service.default_for_consumable(db)
    )
    if warehouse is None:
        raise ValueError("没有唯一可用的耗材仓，请先到设置 → 仓库配置中选择入库仓")
    if warehouse.purpose not in {"consumable", "both"}:
        raise ValueError(f"仓库“{warehouse.name}”当前用途不允许存放耗材")

    existing = db.query(ConsumableReceipt).filter_by(request_key=request_key).first()
    if existing is not None and existing.warehouse_id not in (None, warehouse.id):
        raise ValueError("这次收货已提交到其他仓库，请刷新后重新操作")

    legacy_location = warehouse_service.legacy_location(warehouse)
    row = purchase_svc.receive_purchase(
        db,
        purchase_id,
        request_key=request_key,
        received_on=received_on,
        items=items,
        note=note,
        actor=actor,
        location=legacy_location,
        warehouse_id=warehouse.id,
    )

    receipt = db.query(ConsumableReceipt).filter_by(request_key=request_key).first()
    if receipt is None:
        raise ValueError("收货记录保存失败")
    receipt.warehouse_id = warehouse.id
    db.query(ConsumableTransaction).filter(
        ConsumableTransaction.source_type == "consumable_receipt",
        ConsumableTransaction.source_id == receipt.id,
    ).update({"warehouse_id": warehouse.id}, synchronize_session=False)
    db.commit()
    return row
