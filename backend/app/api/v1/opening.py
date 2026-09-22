from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.db import get_db
from app.services import opening as svc

router = APIRouter(prefix="/opening", tags=["opening"])


@router.get("")
def list_openings(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"summary": svc.opening_dashboard(db), "items": svc.list_openings(db)}


class OpeningBody(BaseModel):
    opening_id: int | None = None
    kind: str
    ref: str = ""
    amount: str | None = None
    quantity: str | None = None
    as_of_date: date | None = None
    note: str = ""


@router.post("")
def upsert(body: OpeningBody, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        row = svc.upsert_opening(
            db, opening_id=body.opening_id, kind=body.kind, ref=body.ref,
            amount=body.amount, quantity=body.quantity,
            as_of_date=body.as_of_date, note=body.note,
            actor=current_actor(request),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": row.id}


@router.delete("/{opening_id}")
def delete(opening_id: int, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    svc.delete_opening(db, opening_id, actor=current_actor(request))
    return {"ok": True}


class AdjustBody(BaseModel):
    delta: str
    reason: str


@router.post("/{opening_id}/adjust")
def adjust(opening_id: int, body: AdjustBody, request: Request,
           db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        adj = svc.adjust(db, opening_id=opening_id, delta=body.delta, reason=body.reason,
                         actor=current_actor(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": adj.id, "delta": str(adj.delta)}
