from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.db import get_db
from app.services import closing as svc

router = APIRouter(prefix="/closing", tags=["closing"])


@router.get("/versions")
def versions(year: int | None = None, month: int | None = None,
             db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return svc.list_versions(db, year=year, month=month)


class CloseBody(BaseModel):
    year: int
    month: int


@router.post("/snapshot")
def create(body: CloseBody, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        row = svc.create_snapshot(db, body.year, body.month, actor=current_actor(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": row.id, "version": row.version, "period": f"{body.year}-{body.month:02d}"}


@router.post("/recalc")
def recalc(body: CloseBody, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """重新计算某月：V1 保留，产生 V{n+1}（规格 1.6）。"""
    try:
        row = svc.recalc(db, body.year, body.month, actor=current_actor(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": row.id, "version": row.version, "period": f"{body.year}-{body.month:02d}",
            "note": "已发送版本不覆盖，本次生成新版本"}
