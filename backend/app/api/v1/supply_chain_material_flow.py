from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.db import get_db
from app.services.production_material_flow_service import (
    consume_materials,
    dispatch_materials,
    list_material_movements,
    receive_materials,
)


router = APIRouter(prefix="/supply-chain", tags=["supply-chain-material-flow"])


class MaterialFlowItemInput(BaseModel):
    reservation_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0)


class MaterialDispatchInput(BaseModel):
    request_key: str = Field(min_length=8, max_length=64)
    carrier: str = Field(default="", max_length=128)
    tracking_no: str = Field(default="", max_length=128)
    note: str = Field(default="", max_length=2000)
    items: list[MaterialFlowItemInput] = Field(min_length=1, max_length=200)


class MaterialReceiveInput(BaseModel):
    request_key: str = Field(min_length=8, max_length=64)
    note: str = Field(default="", max_length=2000)
    items: list[MaterialFlowItemInput] = Field(min_length=1, max_length=200)


class MaterialConsumeInput(BaseModel):
    request_key: str = Field(min_length=8, max_length=64)
    note: str = Field(default="", max_length=2000)
    items: list[MaterialFlowItemInput] = Field(min_length=1, max_length=200)


@router.get("/material-movements")
def material_movements(
    order_id: int | None = Query(None, gt=0),
    movement_type: str = Query("", max_length=24),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        rows = list_material_movements(
            db,
            order_id=order_id,
            movement_type=movement_type,
            limit=limit,
        )
        return {
            "rows": rows,
            "summary": {
                "count": len(rows),
                "dispatchCount": sum(1 for row in rows if row["movementType"] == "dispatch"),
                "receiveCount": sum(1 for row in rows if row["movementType"] == "factory_receive"),
                "consumeCount": sum(1 for row in rows if row["movementType"] == "consume"),
            },
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/production-orders/{order_id}/materials/dispatch")
def dispatch_production_materials(
    order_id: int,
    payload: MaterialDispatchInput,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        rows = dispatch_materials(
            db,
            order_id=order_id,
            items=[
                {"reservation_id": item.reservation_id, "quantity": item.quantity}
                for item in payload.items
            ],
            actor=current_actor(request),
            request_key=payload.request_key,
            carrier=payload.carrier,
            tracking_no=payload.tracking_no,
            note=payload.note,
        )
        return {"rows": rows, "movementNo": rows[0]["movementNo"] if rows else None}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/production-orders/{order_id}/materials/receive")
def receive_production_materials(
    order_id: int,
    payload: MaterialReceiveInput,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        rows = receive_materials(
            db,
            order_id=order_id,
            items=[
                {"reservation_id": item.reservation_id, "quantity": item.quantity}
                for item in payload.items
            ],
            actor=current_actor(request),
            request_key=payload.request_key,
            note=payload.note,
        )
        return {"rows": rows, "movementNo": rows[0]["movementNo"] if rows else None}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/production-orders/{order_id}/materials/consume")
def consume_production_materials(
    order_id: int,
    payload: MaterialConsumeInput,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        rows = consume_materials(
            db,
            order_id=order_id,
            items=[
                {"reservation_id": item.reservation_id, "quantity": item.quantity}
                for item in payload.items
            ],
            actor=current_actor(request),
            request_key=payload.request_key,
            note=payload.note,
        )
        return {"rows": rows, "movementNo": rows[0]["movementNo"] if rows else None}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
