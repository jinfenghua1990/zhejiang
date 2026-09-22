from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.production_purchase_view import list_production_purchase_rows

router = APIRouter(prefix="/supply-chain", tags=["supply-chain"])


@router.get("/production-purchase-view")
def production_purchase_view(
    group: str = Query("all", pattern="^(all|production|transit|receiving|archive)$"),
    stage: str = Query("", max_length=20),
    q: str = Query("", max_length=120),
    limit: int = Query(1000, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict:
    """基于采购主单的只读生产归档视图。

    正品采购按采购状态自动归入生产/在途/到货/完成；耗材采购不会进入此视图。
    不复制、不迁移历史业务数据。
    """
    return list_production_purchase_rows(db, group=group, stage=stage, q=q, limit=limit)
