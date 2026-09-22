from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.core.audit import audit
from app.db import get_db
from app.services import warehouse_purchase_view as purchase_view
from app.services import warehouse_receipt_service
from app.services import warehouse_service as svc

router = APIRouter(prefix="/warehouses", tags=["仓库配置"])


class WarehouseCreateBody(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    warehouse_type: str = "other"
    purpose: str = "both"
    is_sellable: bool = False
    status: str = "active"
    note: str = ""
    jackyun_warehouse_id: str | None = Field(default=None, max_length=64)


class WarehouseUpdateBody(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    warehouse_type: str | None = None
    purpose: str | None = None
    is_sellable: bool | None = None
    status: str | None = None
    note: str | None = None
    # 传空字符串可解除绑定；传 null/不传则保持现有绑定。
    jackyun_warehouse_id: str | None = Field(default=None, max_length=64)


class WarehouseReceiptItemBody(BaseModel):
    item_id: int
    quantity: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]


class WarehouseReceiptBody(BaseModel):
    request_key: UUID
    received_on: date
    warehouse_id: int | None = None
    note: str = ""
    items: list[WarehouseReceiptItemBody] = Field(min_length=1, max_length=100)


@router.get("")
def list_rows(
    include_inactive: bool = Query(True),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return svc.list_warehouses(db, include_inactive=include_inactive)


@router.post("")
def create_row(
    body: WarehouseCreateBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        row = svc.create_warehouse(db, **body.model_dump())
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    audit(db, current_actor(request), "warehouse.create", "warehouses", row.id, {"code": row.code})
    return svc.serialize(row)


@router.patch("/{warehouse_id}")
def update_row(
    warehouse_id: int,
    body: WarehouseUpdateBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        values = {key: value for key, value in body.model_dump().items() if value is not None}
        row = svc.update_warehouse(db, warehouse_id, **values)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    audit(
        db,
        current_actor(request),
        "warehouse.update",
        "warehouses",
        row.id,
        {"fields": sorted(values.keys()), "status": row.status},
    )
    return svc.serialize(row)


@router.delete("/{warehouse_id}")
def delete_row(
    warehouse_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        row = svc.delete_warehouse(db, warehouse_id)
    except LookupError as exc:
        db.rollback()
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc))
    audit(
        db,
        current_actor(request),
        "warehouse.delete",
        "warehouses",
        row.id,
        {"code": row.code, "name": row.name},
    )
    return {"ok": True, "warehouseId": warehouse_id}


@router.post("/consumable-purchases/{purchase_id}/receipts")
def receive_consumable_purchase(
    purchase_id: int,
    body: WarehouseReceiptBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """新收货入口：按可配置仓库登记，旧 /consumables 收货接口继续兼容历史客户端。"""
    try:
        row = warehouse_receipt_service.receive_consumable_purchase(
            db,
            purchase_id,
            request_key=str(body.request_key),
            received_on=body.received_on,
            warehouse_id=body.warehouse_id,
            note=body.note,
            items=[item.model_dump() for item in body.items],
            actor=current_actor(request),
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    audit(
        db,
        current_actor(request),
        "consumable.purchase.receive.warehouse",
        "consumable_purchases",
        row.id,
        {"warehouseId": body.warehouse_id, "requestKey": str(body.request_key)},
    )
    return purchase_view.serialize_purchase(db, row, detail=True)
