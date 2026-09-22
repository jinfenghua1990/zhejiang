from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.core.audit import audit
from app.db import get_db
from app.services import inventory_stocktake_service as svc


router = APIRouter(prefix="/inventory/stocktakes", tags=["库存盘点"])


class StocktakeRef(BaseModel):
    kind: Literal["goods", "consumable"]
    id: int


class CreateStocktakeBody(BaseModel):
    scope: Literal["all", "partial"]
    warehouse_id: int
    item_kinds: list[Literal["goods", "consumable"]] = Field(min_length=1, max_length=2)
    selected_items: list[StocktakeRef] = Field(default_factory=list, max_length=2000)
    search: str = Field(default="", max_length=256)
    category: str = Field(default="", max_length=128)
    note: str = Field(default="", max_length=2000)


class CountItemBody(BaseModel):
    id: int
    actual_qty: str | None = Field(default=None, max_length=64)
    reason: str = Field(default="", max_length=1000)


class SaveCountsBody(BaseModel):
    items: list[CountItemBody] = Field(min_length=1, max_length=2000)


@router.get("")
def list_stocktakes(
    status: str = "",
    limit: int = Query(100, ge=1, le=300),
    db: Session = Depends(get_db),
) -> list[dict]:
    return svc.list_tasks(db, status=status, limit=limit)


@router.get("/candidates")
def candidates(
    warehouse_id: int,
    kinds: str = "goods,consumable",
    search: str = "",
    category: str = "",
    db: Session = Depends(get_db),
) -> list[dict]:
    try:
        item_kinds = [part.strip() for part in kinds.split(",") if part.strip()]
        return svc.stocktake_candidates(
            db,
            warehouse_id=warehouse_id,
            item_kinds=item_kinds,
            search=search,
            category=category,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("")
def create_stocktake(body: CreateStocktakeBody, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        task = svc.create_task(
            db,
            scope=body.scope,
            warehouse_id=body.warehouse_id,
            item_kinds=list(body.item_kinds),
            selected_items=[item.model_dump() for item in body.selected_items],
            search=body.search,
            category=body.category,
            note=body.note,
            actor=current_actor(request),
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit(
        db, current_actor(request), "inventory.stocktake.create", "inventory_stocktake_tasks", task.id,
        {"number": task.number, "scope": task.scope, "warehouseId": task.warehouse_id},
    )
    return svc.task_detail(db, task.id)


@router.get("/{task_id}")
def stocktake_detail(task_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        return svc.task_detail(db, task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{task_id}/counts")
def save_stocktake_counts(
    task_id: int,
    body: SaveCountsBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    try:
        task = svc.save_counts(
            db,
            task_id,
            [item.model_dump() for item in body.items],
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit(
        db, current_actor(request), "inventory.stocktake.count", "inventory_stocktake_tasks", task.id,
        {"status": task.status},
    )
    return svc.task_detail(db, task.id)


@router.post("/{task_id}/confirm")
def confirm_stocktake(task_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        task = svc.confirm_task(db, task_id, actor=current_actor(request))
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit(
        db, current_actor(request), "inventory.stocktake.confirm", "inventory_stocktake_tasks", task.id,
        {"number": task.number},
    )
    return svc.task_detail(db, task.id)


@router.post("/{task_id}/cancel")
def cancel_stocktake(task_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        task = svc.cancel_task(db, task_id)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit(
        db, current_actor(request), "inventory.stocktake.cancel", "inventory_stocktake_tasks", task.id,
        {"number": task.number},
    )
    return svc.task_detail(db, task.id)
