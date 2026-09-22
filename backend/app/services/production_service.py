from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.catalog import ProductSku
from app.models.consumable import Consumable, ConsumableSkuMapping
from app.models.production import ProductionMaterialReservation, ProductionOrder, ProductionOrderItem
from app.services.supplier_sync_service import ensure_supplier
from app.utils.money import to_decimal


OPEN_RESERVATION_STATUSES = {
    "planned",
    "confirmed",
    "producing",
    "produced",
    "shipped",
    "arrived",
    "inbound",
}
PRODUCTION_STATUSES = {
    "planned",
    "confirmed",
    "producing",
    "produced",
    "shipped",
    "arrived",
    "inbound",
    "completed",
    "cancelled",
}


def _qty(value: Decimal | None) -> str:
    return f"{to_decimal(value):f}"


def _order_no() -> str:
    return f"SC-{date.today():%Y%m%d}-{uuid4().hex[:6].upper()}"


def _material_state(row: ProductionMaterialReservation) -> str:
    required = to_decimal(row.required_qty)
    covered = to_decimal(row.reserved_qty) + to_decimal(row.dispatched_qty)
    if covered < required:
        return "shortage"
    if to_decimal(row.consumed_qty) >= required:
        return "consumed"
    if to_decimal(row.factory_received_qty) > 0:
        return "factory"
    if to_decimal(row.dispatched_qty) > 0:
        return "transit"
    return "reserved"


def _serialize_material(row: ProductionMaterialReservation) -> dict:
    required = to_decimal(row.required_qty)
    reserved = to_decimal(row.reserved_qty)
    dispatched = to_decimal(row.dispatched_qty)
    shortage = max(required - reserved - dispatched, Decimal("0"))
    return {
        "id": row.id,
        "consumableId": row.consumable_id,
        "code": row.code,
        "name": row.name,
        "unit": row.unit,
        "requiredQty": _qty(required),
        "reservedQty": _qty(reserved),
        "dispatchedQty": _qty(dispatched),
        "factoryReceivedQty": _qty(row.factory_received_qty),
        "consumedQty": _qty(row.consumed_qty),
        "shortageQty": _qty(shortage),
        "state": _material_state(row),
    }


def _serialize_item(row: ProductionOrderItem) -> dict:
    planned = to_decimal(row.quantity)
    completed = to_decimal(row.completed_qty)
    shipped = to_decimal(row.shipped_qty)
    arrived = to_decimal(row.arrived_qty)
    inbound = to_decimal(row.inbound_qty)
    return {
        "id": row.id,
        "skuId": row.sku_id,
        "skuCode": row.sku_code,
        "skuName": row.sku_name,
        "unit": row.unit,
        "quantity": _qty(planned),
        "completedQty": _qty(completed),
        "remainingProductionQty": _qty(max(planned - completed, Decimal("0"))),
        "factoryReadyQty": _qty(max(completed - shipped, Decimal("0"))),
        "shippedQty": _qty(shipped),
        "transitQty": _qty(max(shipped - arrived, Decimal("0"))),
        "arrivedQty": _qty(arrived),
        "pendingInboundQty": _qty(max(arrived - inbound, Decimal("0"))),
        "inboundQty": _qty(inbound),
    }


def _order_payload(
    order: ProductionOrder,
    items: list[ProductionOrderItem],
    materials: list[ProductionMaterialReservation],
) -> dict:
    shortage_count = sum(1 for row in materials if _material_state(row) == "shortage")
    return {
        "id": order.id,
        "orderNo": order.order_no,
        "factoryName": order.factory_name,
        "status": order.status,
        "plannedStartDate": order.planned_start_date.isoformat() if order.planned_start_date else None,
        "expectedDeliveryDate": order.expected_delivery_date.isoformat() if order.expected_delivery_date else None,
        "sourceType": order.source_type,
        "note": order.note,
        "createdBy": order.created_by,
        "createdAt": order.created_at.isoformat() if order.created_at else None,
        "updatedAt": order.updated_at.isoformat() if order.updated_at else None,
        "itemCount": len(items),
        "materialCount": len(materials),
        "materialShortageCount": shortage_count,
        "items": [_serialize_item(row) for row in items],
        "materials": [_serialize_material(row) for row in materials],
    }


def production_order_detail(db: Session, order: ProductionOrder) -> dict:
    items = db.query(ProductionOrderItem).filter_by(production_order_id=order.id).order_by(ProductionOrderItem.id).all()
    materials = (
        db.query(ProductionMaterialReservation)
        .filter_by(production_order_id=order.id)
        .order_by(ProductionMaterialReservation.code, ProductionMaterialReservation.id)
        .all()
    )
    return _order_payload(order, items, materials)


def production_order_details(db: Session, orders: list[ProductionOrder]) -> list[dict]:
    """批量组装多个生产单详情，避免逐单查询造成 N+1。"""
    if not orders:
        return []
    order_ids = [order.id for order in orders]
    items = (
        db.query(ProductionOrderItem)
        .filter(ProductionOrderItem.production_order_id.in_(order_ids))
        .order_by(ProductionOrderItem.id)
        .all()
    )
    materials = (
        db.query(ProductionMaterialReservation)
        .filter(ProductionMaterialReservation.production_order_id.in_(order_ids))
        .order_by(ProductionMaterialReservation.code, ProductionMaterialReservation.id)
        .all()
    )
    items_by_order: dict[int, list[ProductionOrderItem]] = defaultdict(list)
    materials_by_order: dict[int, list[ProductionMaterialReservation]] = defaultdict(list)
    for item in items:
        items_by_order[item.production_order_id].append(item)
    for row in materials:
        materials_by_order[row.production_order_id].append(row)
    return [
        _order_payload(
            order,
            items_by_order.get(order.id, []),
            materials_by_order.get(order.id, []),
        )
        for order in orders
    ]


def list_production_orders(db: Session, *, status: str = "", limit: int = 200) -> list[dict]:
    query = db.query(ProductionOrder)
    if status:
        query = query.filter(ProductionOrder.status == status)
    orders = query.order_by(ProductionOrder.created_at.desc(), ProductionOrder.id.desc()).limit(limit).all()
    return production_order_details(db, orders)


def _recalculate_materials(db: Session, order: ProductionOrder) -> list[ProductionMaterialReservation]:
    existing = db.query(ProductionMaterialReservation).filter_by(production_order_id=order.id).all()
    if any(
        to_decimal(row.dispatched_qty) > 0
        or to_decimal(row.factory_received_qty) > 0
        or to_decimal(row.consumed_qty) > 0
        for row in existing
    ):
        raise ValueError("该生产单已有耗材发出/到厂/消耗记录，不能直接重算预占")
    for row in existing:
        db.delete(row)
    db.flush()

    items = db.query(ProductionOrderItem).filter_by(production_order_id=order.id).all()
    item_qty = {item.sku_id: to_decimal(item.quantity) for item in items}
    if not item_qty:
        return []

    mappings = db.query(ConsumableSkuMapping).filter(ConsumableSkuMapping.sku_id.in_(list(item_qty))).all()
    required_by_consumable: dict[int, Decimal] = {}
    for mapping in mappings:
        usage = to_decimal(mapping.usage_per_unit)
        if usage <= 0:
            continue
        required_by_consumable[mapping.consumable_id] = (
            required_by_consumable.get(mapping.consumable_id, Decimal("0"))
            + item_qty[mapping.sku_id] * usage
        )
    if not required_by_consumable:
        return []

    material_ids = sorted(required_by_consumable)
    materials = db.scalars(
        select(Consumable)
        .where(Consumable.id.in_(material_ids))
        .order_by(Consumable.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    material_map = {row.id: row for row in materials}

    other_reserved_rows = (
        db.query(
            ProductionMaterialReservation.consumable_id,
            func.coalesce(func.sum(ProductionMaterialReservation.reserved_qty), 0),
        )
        .join(ProductionOrder, ProductionOrder.id == ProductionMaterialReservation.production_order_id)
        .filter(
            ProductionMaterialReservation.consumable_id.in_(material_ids),
            ProductionOrder.id != order.id,
            ProductionOrder.status.in_(OPEN_RESERVATION_STATUSES),
        )
        .group_by(ProductionMaterialReservation.consumable_id)
        .all()
    )
    other_reserved = {int(material_id): to_decimal(qty) for material_id, qty in other_reserved_rows}

    rows: list[ProductionMaterialReservation] = []
    for material_id in material_ids:
        material = material_map.get(material_id)
        if material is None:
            continue
        required = required_by_consumable[material_id]
        available = max(to_decimal(material.stock_qty) - other_reserved.get(material_id, Decimal("0")), Decimal("0"))
        reserved = min(required, available)
        row = ProductionMaterialReservation(
            production_order_id=order.id,
            consumable_id=material.id,
            code=material.code,
            name=material.name,
            unit=material.unit,
            required_qty=required,
            reserved_qty=reserved,
            dispatched_qty=Decimal("0"),
            factory_received_qty=Decimal("0"),
            consumed_qty=Decimal("0"),
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def create_production_order(
    db: Session,
    *,
    factory_name: str,
    items: list[dict],
    actor: str,
    planned_start_date: date | None = None,
    expected_delivery_date: date | None = None,
    source_type: str = "manual",
    note: str = "",
) -> ProductionOrder:
    factory = factory_name.strip()
    if not factory:
        raise ValueError("请选择或填写工厂")
    if not items:
        raise ValueError("生产单至少需要一个 SKU")
    if planned_start_date and expected_delivery_date and expected_delivery_date < planned_start_date:
        raise ValueError("预计交货日期不能早于计划开始日期")

    quantities: dict[int, Decimal] = {}
    for item in items:
        try:
            sku_id = int(item.get("sku_id"))
            qty = to_decimal(item.get("quantity"))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("生产 SKU 明细格式不正确") from exc
        if not qty.is_finite() or qty <= 0:
            raise ValueError("生产数量必须大于 0")
        quantities[sku_id] = quantities.get(sku_id, Decimal("0")) + qty

    sku_ids = sorted(quantities)
    skus = db.scalars(select(ProductSku).where(ProductSku.id.in_(sku_ids)).order_by(ProductSku.id)).all()
    sku_map = {row.id: row for row in skus}
    missing = [sku_id for sku_id in sku_ids if sku_id not in sku_map]
    if missing:
        raise ValueError(f"正品 SKU 不存在：{', '.join(str(value) for value in missing)}")
    inactive = [row.sku_code for row in skus if row.status != "active"]
    if inactive:
        raise ValueError(f"以下 SKU 当前不是启用状态：{', '.join(inactive)}")

    order = ProductionOrder(
        order_no=_order_no(),
        factory_name=factory,
        status="planned",
        planned_start_date=planned_start_date,
        expected_delivery_date=expected_delivery_date,
        source_type=source_type.strip() or "manual",
        note=note.strip(),
        created_by=actor,
    )
    db.add(order)
    db.flush()
    for sku_id in sku_ids:
        sku = sku_map[sku_id]
        db.add(
            ProductionOrderItem(
                production_order_id=order.id,
                sku_id=sku.id,
                sku_code=sku.sku_code,
                sku_name=sku.sku_name or "",
                unit=sku.unit or "",
                quantity=quantities[sku_id],
                completed_qty=Decimal("0"),
                shipped_qty=Decimal("0"),
                arrived_qty=Decimal("0"),
                inbound_qty=Decimal("0"),
            )
        )
    db.flush()
    _recalculate_materials(db, order)
    ensure_supplier(db, order.factory_name, platform="线下")
    db.commit()
    db.refresh(order)
    return order


def recalculate_production_materials(db: Session, order_id: int) -> ProductionOrder:
    order = db.scalar(select(ProductionOrder).where(ProductionOrder.id == order_id).with_for_update())
    if order is None:
        raise ValueError("生产单不存在")
    if order.status not in {"planned", "confirmed"}:
        raise ValueError("只有计划中/已确认的生产单可以重算耗材预占")
    _recalculate_materials(db, order)
    db.commit()
    db.refresh(order)
    return order


def cancel_production_order(db: Session, order_id: int) -> ProductionOrder:
    order = db.scalar(select(ProductionOrder).where(ProductionOrder.id == order_id).with_for_update())
    if order is None:
        raise ValueError("生产单不存在")
    items = db.scalars(
        select(ProductionOrderItem)
        .where(ProductionOrderItem.production_order_id == order.id)
        .order_by(ProductionOrderItem.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    if any(to_decimal(item.completed_qty) > 0 for item in items):
        raise ValueError("该生产单已有成品生产完成记录，不能取消")
    materials = db.query(ProductionMaterialReservation).filter_by(production_order_id=order.id).all()
    if any(to_decimal(row.dispatched_qty) > 0 for row in materials):
        raise ValueError("该生产单已有耗材发往工厂，需先处理在途/退料后再取消")
    order.status = "cancelled"
    db.commit()
    db.refresh(order)
    return order
