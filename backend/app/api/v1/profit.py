from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.db import get_db
from app.services import profit as ps

router = APIRouter(prefix="/profit", tags=["profit"])


class CostBody(BaseModel):
    sku_id: int
    period_year: int
    period_month: int
    actual_cost: str | None = None
    purch_order_cost: str | None = None
    default_cost: str | None = None
    estimated_cost: str | None = None
    source: str | None = None


@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> dict[str, Any]:
    return ps.overview(db)


@router.get("/costs")
def list_costs(period_year: int | None = None, period_month: int | None = None,
               db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return ps.list_costs(db, period_year=period_year, period_month=period_month)


@router.post("/costs")
def upsert_cost(body: CostBody, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        row = ps.upsert_cost(
            db, sku_id=body.sku_id, period_year=body.period_year, period_month=body.period_month,
            actual_cost=body.actual_cost, purch_order_cost=body.purch_order_cost,
            default_cost=body.default_cost, estimated_cost=body.estimated_cost,
            source=body.source,
            actor=current_actor(request),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": row.id, "version": row.version}


@router.get("/compute")
def compute(period_year: int, period_month: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return ps.compute(db, period_year, period_month)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
