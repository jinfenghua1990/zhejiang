"""耗材库存账本核心：流水类型、幂等边界与扣减/增加规则。

从 consumable_service 拆出，独立于主档/映射管理。本模块只依赖 models 与
warehouse_service，不反向依赖 consumable_service，避免循环导入。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.catalog import Warehouse
from app.models.consumable import Consumable, ConsumableTransaction
from app.utils.money import to_decimal

TX_TYPES = {
    "purchase": "采购入库",
    "send_factory": "发往工厂",
    "factory_receive": "工厂收货",
    "consume": "消耗",
    "stocktake": "盘点",
    "loss": "报损",
    "manual": "手工调整",
    # 旧类型别名：adjustment 与 stocktake 同义（有符号差额，记自有仓）
    "adjustment": "盘点",
}
_SIGNED_TX_TYPES = {"stocktake", "adjustment", "manual"}


def record_transaction(
    db: Session,
    *,
    consumable_id: int,
    transaction_type: str,
    quantity: str,
    unit_cost: str | None = None,
    source_type: str = "manual",
    source_id: int | None = None,
    note: str = "",
    request_key: str | None = None,
    commit: bool = True,
    location: str = "own",
    warehouse_id: int | None = None,
) -> ConsumableTransaction:
    row = db.scalar(select(Consumable).where(Consumable.id == consumable_id).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise ValueError("耗材不存在")
    if transaction_type not in TX_TYPES:
        raise ValueError(f"流水类型必须是 {'、'.join(TX_TYPES)} 之一")
    if location is None:
        location = "own"
    if location not in {"own", "factory"}:
        raise ValueError("库存位置必须是 own（自有仓）或 factory（工厂）")
    warehouse = None
    if warehouse_id is not None:
        warehouse = db.get(Warehouse, warehouse_id)
        if warehouse is None or warehouse.status != "active":
            raise ValueError("所选仓库不存在或已停用")
        if warehouse.purpose not in {"consumable", "both"}:
            raise ValueError(f"仓库“{warehouse.name}”当前用途不允许存放耗材")
        # warehouse_id 是新口径的事实来源；旧 location 只保留兼容展示，不能
        # 让调用方把工厂仓流水误写成自有仓，或反过来。
        from app.services import warehouse_service
        location = warehouse_service.legacy_location(warehouse)
    qty = to_decimal(quantity)
    cost = to_decimal(unit_cost) if unit_cost not in (None, "") else None
    if not qty.is_finite() or (cost is not None and (not cost.is_finite() or cost < 0)):
        raise ValueError("数量必须是有效数字，成本不能为负")
    if transaction_type not in _SIGNED_TX_TYPES and qty <= 0:
        raise ValueError("该流水类型的数量必须大于 0")
    if transaction_type in _SIGNED_TX_TYPES and qty == 0:
        raise ValueError("调整数量不能为 0")
    if request_key:
        existing = db.query(ConsumableTransaction).filter_by(request_key=request_key).first()
        if existing:
            if (
                existing.consumable_id,
                existing.transaction_type,
                existing.quantity,
                existing.unit_cost,
                existing.warehouse_id,
                existing.note,
            ) != (
                consumable_id,
                transaction_type,
                qty,
                cost,
                warehouse_id,
                note.strip(),
            ):
                raise ValueError("这次登记已提交过不同内容，请刷新后重新操作")
            return existing
    if source_type != "manual" and source_id is not None:
        existing = db.query(ConsumableTransaction).filter_by(
            source_type=source_type, source_id=source_id, consumable_id=consumable_id
        ).first()
        if existing:
            expected_location = (
                None
                if transaction_type == "send_factory"
                else "factory"
                if transaction_type == "factory_receive"
                else location
            )
            if (
                existing.transaction_type,
                to_decimal(existing.quantity),
                to_decimal(existing.unit_cost) if existing.unit_cost is not None else None,
                existing.warehouse_id,
                existing.location or None,
                existing.note or "",
            ) != (
                transaction_type,
                qty,
                cost,
                warehouse_id,
                expected_location,
                note.strip(),
            ):
                raise ValueError("同一业务来源已经写入不同的耗材流水，请先核对原始单据")
            return existing

    stock_before = to_decimal(row.stock_qty)
    factory_before = to_decimal(row.factory_qty)
    transit = to_decimal(row.transit_qty)
    tx_location: str | None = None
    if transaction_type == "purchase":
        # 到货位置：自有仓或工厂（耗材不进吉客云，收货即入本平台对应库存）
        tx_location = location
        if location == "factory":
            row.factory_qty = factory_before + qty
        else:
            row.stock_qty = stock_before + qty
        row.purchased_qty = to_decimal(row.purchased_qty) + qty
    elif transaction_type == "send_factory":
        # 发往工厂是实际库存转移，不能像历史补录 consume 一样允许负库存。
        if stock_before - qty < 0:
            raise ValueError(f"自有仓库存不足（当前库存 {stock_before}），不能发往工厂 {qty}")
        row.stock_qty = stock_before - qty
        row.transit_qty = transit + qty
    elif transaction_type == "factory_receive":
        # 工厂收货：在途 → 工厂库存
        if transit - qty < 0:
            raise ValueError(f"在途库存不足（当前在途 {transit}），请先登记发往工厂")
        tx_location = "factory"
        row.transit_qty = transit - qty
        row.factory_qty = factory_before + qty
    elif transaction_type == "consume":
        tx_location = location
        if location == "factory":
            if factory_before - qty < 0:
                raise ValueError(f"工厂库存不足（当前工厂库存 {factory_before}），不能消耗 {qty}")
            row.factory_qty = factory_before - qty
        else:
            # 自有仓历史补录允许暂时出现负库存，后续采购入库或盘点补齐。
            row.stock_qty = stock_before - qty
        row.used_qty = to_decimal(row.used_qty) + qty
    elif transaction_type == "loss":
        tx_location = location
        if location == "factory":
            if factory_before - qty < 0:
                raise ValueError(f"工厂库存不足（当前工厂库存 {factory_before}），不能报损 {qty}")
            row.factory_qty = factory_before - qty
        else:
            if stock_before - qty < 0:
                raise ValueError(f"自有仓库存不足（当前库存 {stock_before}），不能报损 {qty}")
            row.stock_qty = stock_before - qty
    else:  # stocktake / adjustment / manual：有符号差额
        tx_location = location
        if location == "factory":
            row.factory_qty = factory_before + qty
        else:
            row.stock_qty = stock_before + qty
    tx = ConsumableTransaction(
        consumable_id=consumable_id,
        transaction_type=transaction_type,
        quantity=qty,
        unit_cost=cost,
        warehouse_id=warehouse_id,
        location=tx_location,
        source_type=source_type,
        source_id=source_id,
        stock_before=stock_before,
        stock_after=to_decimal(row.stock_qty),
        factory_before=factory_before,
        factory_after=to_decimal(row.factory_qty),
        request_key=request_key,
        note=note.strip(),
        occurred_at=datetime.now(timezone.utc),
    )
    db.add(tx)
    if commit:
        db.commit()
        db.refresh(tx)
    else:
        db.flush()
    return tx