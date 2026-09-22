"""吉客云销售订单三通道同步中心。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.core.audit import audit
from app.db import get_db
from app.models.integration import SyncJob
from app.services.jky_order_sync_service import JOB_PROVIDER, JOB_TYPE, order_sync_status

router = APIRouter(prefix="/jky-orders", tags=["吉客云订单三通道"])


@router.get("/status")
def get_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    return order_sync_status(db)


@router.post("/sync")
def start_sync(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.tasks.sync import sync_jky_orders

    task = sync_jky_orders.delay()
    audit(db, current_actor(request), "jky_order.sync_queued", "celery_task", task.id)
    return {
        "ok": True,
        "taskId": task.id,
        "status": "queued",
        "message": "已发起吉客云订单同步：Web → Windows RPA → OpenAPI/MCP",
    }


@router.get("/jobs")
def recent_jobs(limit: int = 10, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    limit = min(max(limit, 1), 50)
    rows = (
        db.query(SyncJob)
        .filter_by(provider=JOB_PROVIDER, job_type=JOB_TYPE)
        .order_by(SyncJob.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": row.id,
            "jobType": row.job_type,
            "status": row.status,
            "startedAt": row.started_at.isoformat() if row.started_at else None,
            "finishedAt": row.finished_at.isoformat() if row.finished_at else None,
            "stats": row.stats or {},
            "errorSummary": row.error_summary or "",
        }
        for row in rows
    ]
