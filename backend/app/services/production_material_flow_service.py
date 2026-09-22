from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.consumable import Consumable
from app.models.production import (
    ProductionMaterialMovement,
    ProductionMaterialReservation,
    ProductionOrder,
)
from app.services.consumable_service import record_transaction
from app.utils.money import to_decimal


FLOW_ORDER_STATUSES = {"planned", "confirmed", "producing"}
CONSUME_ORDER_STATUSES = FLOW_ORDER_STATUSES | {"produced", "shipped", "arrived", "inbound", "completed"}
MOVEMENT_TYPES = {"dispatch", "factory_receive", "consume"}


def _qty(value: Decimal | None) -> str:
    return f"{to_decimal(value):f}"


def _movement_no(kind: str) -> str:
    prefix = {"dispatch": "OUT", "factory_receive": "RCV", "consume": "USE"}[kind]
    return f"{prefix}-{datetime.now(timezone.utc):%Y%m%d}-{uuid4().hex[:6].upper()}"


def _normalize_items(items: list[dict]) -> dict[int, Decimal]:
    if not items:
        raise ValueError("至少需要一条耗材明细")
    normalized: dict[int, Decimal] = {}
    for item in items:
        try:
            reservation_id = int(item.get("reservation_id"))
            quantity = to_decimal(item.get("quantity"))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("耗材流转明细格式不正确") from exc
        if reservation_id <= 0 or not quantity.is_finite() or quantity <= 0:
            raise ValueError("耗材流转数量必须大于 0")
        normalized[reservation_id] = normalized.get(reservation_id, Decimal("0")) + quantity
    return normalized


def _serialize_movement(row: ProductionMaterialMovement, reservation: ProductionMaterialReservation | None = None) -> dict:
    return {
        "id": row.id,
        "movementNo": row.movement_no,
        "requestKey": row.request_key,
        "productionOrderId": row.production_order_id,
        "reservationId": row.reservation_id,
        "consumableId": row.consumable_id,
        "consumableCode": reservation.code if reservation else "",
        "consumableName": reservation.name if reservation else "",
        "unit": reservation.unit if reservation else "",
        "movementType": row.movement_type,
        "quantity": _qty(row.quantity),
        "carrier": row.carrier,
        "trackingNo": row.tracking_no,
        "note": row.note,
        "actor": row.actor,
        "occurredAt": row.occurred_at.isoformat() if row.occurred_at else None,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def list_material_movements(
    db: Session,
    *,
    order_id: int | None = None,
    movement_type: str = "",
    limit: int = 500,
) -> list[dict]:
    query = db.query(ProductionMaterialMovement, ProductionMaterialReservation).join(
        ProductionMaterialReservation,
        ProductionMaterialReservation.id == ProductionMaterialMovement.reservation_id,
    )
    if order_id is not None:
        query = query.filter(ProductionMaterialMovement.production_order_id == order_id)
    if movement_type:
        if movement_type not in MOVEMENT_TYPES:
            raise ValueError("耗材流转类型不正确")
        query = query.filter(ProductionMaterialMovement.movement_type == movement_type)
    rows = query.order_by(
        ProductionMaterialMovement.occurred_at.desc(),
        ProductionMaterialMovement.id.desc(),
    ).limit(limit).all()
    return [_serialize_movement(movement, reservation) for movement, reservation in rows]


def _idempotent_existing(
    db: Session,
    *,
    order_id: int,
    movement_type: str,
    request_key: str,
    requested: dict[int, Decimal],
) -> list[ProductionMaterialMovement] | None:
    existing = (
        db.query(ProductionMaterialMovement)
        .filter_by(
            production_order_id=order_id,
            movement_type=movement_type,
            request_key=request_key,
        )
        .order_by(ProductionMaterialMovement.reservation_id)
        .all()
    )
    if not existing:
        return None
    actual = {row.reservation_id: to_decimal(row.quantity) for row in existing}
    if actual != requested:
        raise ValueError("该请求编号已经提交过不同的耗材数量，请刷新页面后重试")
    return existing


def _locked_reservations(
    db: Session,
    *,
    order_id: int,
    reservation_ids: list[int],
) -> tuple[list[ProductionMaterialReservation], dict[int, ProductionMaterialReservation]]:
    reservations = db.scalars(
        select(ProductionMaterialReservation)
        .where(
            ProductionMaterialReservation.id.in_(reservation_ids),
            ProductionMaterialReservation.production_order_id == order_id,
        )
        .order_by(ProductionMaterialReservation.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    reservation_map = {row.id: row for row in reservations}
    missing = [value for value in reservation_ids if value not in reservation_map]
    if missing:
        raise ValueError(f"生产耗材明细不存在：{', '.join(str(value) for value in missing)}")
    return reservations, reservation_map


def dispatch_materials(
    db: Session,
    *,
    order_id: int,
    items: list[dict],
    actor: str,
    request_key: str,
    carrier: str = "",
    tracking_no: str = "",
    note: str = "",
) -> list[dict]:
    requested = _normalize_items(items)
    key = request_key.strip()
    if not key:
        raise ValueError("缺少请求编号，请刷新页面后重试")

    existing = _idempotent_existing(
        db,
        order_id=order_id,
        movement_type="dispatch",
        request_key=key,
        requested=requested,
    )
    if existing is not None:
        reservations = {
            row.id: row for row in db.query(ProductionMaterialReservation).filter(
                ProductionMaterialReservation.id.in_([item.reservation_id for item in existing])
            ).all()
        }
        return [_serialize_movement(row, reservations.get(row.reservation_id)) for row in existing]

    order = db.scalar(select(ProductionOrder).where(ProductionOrder.id == order_id).with_for_update())
    if order is None:
        raise ValueError("生产单不存在")
    if order.status not in FLOW_ORDER_STATUSES:
        raise ValueError("当前生产单状态不能发耗材")

    reservation_ids = sorted(requested)
    reservations, reservation_map = _locked_reservations(
        db,
        order_id=order_id,
        reservation_ids=reservation_ids,
    )

    material_ids = sorted({row.consumable_id for row in reservations})
    materials = db.scalars(
        select(Consumable)
        .where(Consumable.id.in_(material_ids))
        .order_by(Consumable.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    material_map = {row.id: row for row in materials}

    for reservation_id in reservation_ids:
        reservation = reservation_map[reservation_id]
        quantity = requested[reservation_id]
        if quantity > to_decimal(reservation.reserved_qty):
            raise ValueError(
                f"{reservation.code} 本次发料 {quantity} 超过当前预占 {to_decimal(reservation.reserved_qty)}"
            )
        material = material_map.get(reservation.consumable_id)
        if material is None:
            raise ValueError(f"耗材 {reservation.code} 不存在")
        if quantity > to_decimal(material.stock_qty):
            raise ValueError(
                f"{reservation.code} 自有仓库存不足：当前 {to_decimal(material.stock_qty)}，本次需要 {quantity}"
            )

    movement_no = _movement_no("dispatch")
    now = datetime.now(timezone.utc)
    movements: list[ProductionMaterialMovement] = []
    for reservation_id in reservation_ids:
        reservation = reservation_map[reservation_id]
        quantity = requested[reservation_id]
        movement = ProductionMaterialMovement(
            movement_no=movement_no,
            request_key=key,
            production_order_id=order.id,
            reservation_id=reservation.id,
            consumable_id=reservation.consumable_id,
            movement_type="dispatch",
            quantity=quantity,
            carrier=carrier.strip(),
            tracking_no=tracking_no.strip(),
            note=note.strip(),
            actor=actor,
            occurred_at=now,
        )
        db.add(movement)
        db.flush()
        record_transaction(
            db,
            consumable_id=reservation.consumable_id,
            transaction_type="send_factory",
            quantity=str(quantity),
            source_type="production_material_movement",
            source_id=movement.id,
            note=f"生产单 {order.order_no} 发往 {order.factory_name}; {note.strip()}".rstrip("; "),
            commit=False,
        )
        reservation.reserved_qty = to_decimal(reservation.reserved_qty) - quantity
        reservation.dispatched_qty = to_decimal(reservation.dispatched_qty) + quantity
        movements.append(movement)

    if order.status == "planned":
        order.status = "confirmed"
    db.commit()
    return [_serialize_movement(row, reservation_map.get(row.reservation_id)) for row in movements]


def receive_materials(
    db: Session,
    *,
    order_id: int,
    items: list[dict],
    actor: str,
    request_key: str,
    note: str = "",
) -> list[dict]:
    requested = _normalize_items(items)
    key = request_key.strip()
    if not key:
        raise ValueError("缺少请求编号，请刷新页面后重试")

    existing = _idempotent_existing(
        db,
        order_id=order_id,
        movement_type="factory_receive",
        request_key=key,
        requested=requested,
    )
    if existing is not None:
        reservations = {
            row.id: row for row in db.query(ProductionMaterialReservation).filter(
                ProductionMaterialReservation.id.in_([item.reservation_id for item in existing])
            ).all()
        }
        return [_serialize_movement(row, reservations.get(row.reservation_id)) for row in existing]

    order = db.scalar(select(ProductionOrder).where(ProductionOrder.id == order_id).with_for_update())
    if order is None:
        raise ValueError("生产单不存在")
    if order.status not in FLOW_ORDER_STATUSES:
        raise ValueError("当前生产单状态不能登记工厂签收")

    reservation_ids = sorted(requested)
    _, reservation_map = _locked_reservations(
        db,
        order_id=order_id,
        reservation_ids=reservation_ids,
    )

    for reservation_id in reservation_ids:
        reservation = reservation_map[reservation_id]
        quantity = requested[reservation_id]
        outstanding = to_decimal(reservation.dispatched_qty) - to_decimal(reservation.factory_received_qty)
        if quantity > outstanding:
            raise ValueError(
                f"{reservation.code} 本次签收 {quantity} 超过该生产单在途 {outstanding}"
            )

    movement_no = _movement_no("factory_receive")
    now = datetime.now(timezone.utc)
    movements: list[ProductionMaterialMovement] = []
    for reservation_id in reservation_ids:
        reservation = reservation_map[reservation_id]
        quantity = requested[reservation_id]
        movement = ProductionMaterialMovement(
            movement_no=movement_no,
            request_key=key,
            production_order_id=order.id,
            reservation_id=reservation.id,
            consumable_id=reservation.consumable_id,
            movement_type="factory_receive",
            quantity=quantity,
            note=note.strip(),
            actor=actor,
            occurred_at=now,
        )
        db.add(movement)
        db.flush()
        record_transaction(
            db,
            consumable_id=reservation.consumable_id,
            transaction_type="factory_receive",
            quantity=str(quantity),
            source_type="production_material_movement",
            source_id=movement.id,
            note=f"生产单 {order.order_no} 工厂 {order.factory_name} 签收; {note.strip()}".rstrip("; "),
            commit=False,
        )
        reservation.factory_received_qty = to_decimal(reservation.factory_received_qty) + quantity
        movements.append(movement)

    if order.status == "planned":
        order.status = "confirmed"
    db.commit()
    return [_serialize_movement(row, reservation_map.get(row.reservation_id)) for row in movements]


def consume_materials(
    db: Session,
    *,
    order_id: int,
    items: list[dict],
    actor: str,
    request_key: str,
    note: str = "",
) -> list[dict]:
    """登记工厂实际耗材消耗：只扣工厂库存，不再误扣自有仓库存。"""
    requested = _normalize_items(items)
    key = request_key.strip()
    if not key:
        raise ValueError("缺少请求编号，请刷新页面后重试")

    existing = _idempotent_existing(
        db,
        order_id=order_id,
        movement_type="consume",
        request_key=key,
        requested=requested,
    )
    if existing is not None:
        reservations = {
            row.id: row for row in db.query(ProductionMaterialReservation).filter(
                ProductionMaterialReservation.id.in_([item.reservation_id for item in existing])
            ).all()
        }
        return [_serialize_movement(row, reservations.get(row.reservation_id)) for row in existing]

    order = db.scalar(select(ProductionOrder).where(ProductionOrder.id == order_id).with_for_update())
    if order is None:
        raise ValueError("生产单不存在")
    if order.status not in CONSUME_ORDER_STATUSES:
        raise ValueError("当前生产单状态不能登记工厂耗材消耗")

    reservation_ids = sorted(requested)
    reservations, reservation_map = _locked_reservations(
        db,
        order_id=order_id,
        reservation_ids=reservation_ids,
    )
    material_ids = sorted({row.consumable_id for row in reservations})
    materials = db.scalars(
        select(Consumable)
        .where(Consumable.id.in_(material_ids))
        .order_by(Consumable.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    material_map = {row.id: row for row in materials}

    for reservation_id in reservation_ids:
        reservation = reservation_map[reservation_id]
        quantity = requested[reservation_id]
        usable = to_decimal(reservation.factory_received_qty) - to_decimal(reservation.consumed_qty)
        if quantity > usable:
            raise ValueError(
                f"{reservation.code} 本次消耗 {quantity} 超过该生产单工厂可用耗材 {usable}"
            )
        material = material_map.get(reservation.consumable_id)
        if material is None:
            raise ValueError(f"耗材 {reservation.code} 不存在")
        if quantity > to_decimal(material.factory_qty):
            raise ValueError(
                f"{reservation.code} 工厂库存不足：当前 {to_decimal(material.factory_qty)}，本次需要 {quantity}"
            )

    movement_no = _movement_no("consume")
    now = datetime.now(timezone.utc)
    movements: list[ProductionMaterialMovement] = []
    for reservation_id in reservation_ids:
        reservation = reservation_map[reservation_id]
        quantity = requested[reservation_id]
        movement = ProductionMaterialMovement(
            movement_no=movement_no,
            request_key=key,
            production_order_id=order.id,
            reservation_id=reservation.id,
            consumable_id=reservation.consumable_id,
            movement_type="consume",
            quantity=quantity,
            note=note.strip(),
            actor=actor,
            occurred_at=now,
        )
        db.add(movement)
        db.flush()
        record_transaction(
            db,
            consumable_id=reservation.consumable_id,
            transaction_type="consume",
            quantity=str(quantity),
            source_type="production_material_movement",
            source_id=movement.id,
            note=f"生产单 {order.order_no} 工厂 {order.factory_name} 实际消耗; {note.strip()}".rstrip("; "),
            commit=False,
            location="factory",
        )
        reservation.consumed_qty = to_decimal(reservation.consumed_qty) + quantity
        movements.append(movement)

    db.commit()
    return [_serialize_movement(row, reservation_map.get(row.reservation_id)) for row in movements]
