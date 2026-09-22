"""吉客云 Web 连接器：状态 / 更新登录态（粘贴 cURL）/ 立即同步 / 同步日志。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.adapters.jky_web import JkyWebError, PROVIDER
from app.api.deps import current_actor
from app.core.audit import audit
from app.db import get_db
from app.services.jky_web_sync_service import sync_status, update_session

router = APIRouter(prefix="/jky-web", tags=["吉客云Web连接器"])


class CurlInput(BaseModel):
    curl: str


@router.get("/status")
def get_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    """连接状态卡片（只读本地库，不触发外部请求）。"""
    return sync_status(db)


@router.post("/session")
def post_session(payload: CurlInput, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """更新网页登录态：用户在吉客云 F12 → Network → Copy as cURL 后粘贴。"""
    if not payload.curl or not payload.curl.strip():
        raise HTTPException(status_code=400, detail="cURL 内容为空")
    try:
        return update_session(db, payload.curl, actor=current_actor(request))
    except JkyWebError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sync")
def start_sync(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """兼容旧入口：销售订单走三通道，前端可轮询 /jky-orders/jobs。"""
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
    """最近的吉客云同步任务（轮询用）。"""
    from app.models.integration import SyncJob

    limit = min(max(limit, 1), 50)
    rows = (
        db.query(SyncJob)
        .filter(SyncJob.provider.in_((PROVIDER, "jky_order")))
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
