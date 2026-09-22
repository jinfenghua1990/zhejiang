"""吉客云采购入库轻量跟踪：只抓采购入库主/明细并刷新 1688 备注关联。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.adapters.jackyun import finish_sync_job, start_sync_job
from app.adapters.jky_web import (
    JkyWebClient,
    JkyWebError,
    JkyWebSessionManager,
    PROVIDER,
    SESSION_ACTIVE,
    SESSION_ERROR,
    SESSION_NEED_LOGIN,
    SESSION_NEED_VERIFY,
)
from app.celery_app import celery_app
from app.config import settings
from app.db import SessionLocal
from app.models.integration import SyncLog
from app.services.alibaba1688_remark_match_service import run_verified_remark_match
from app.services.jky_web_sync_service import sync_stockin


@celery_app.task(
    name="tasks.sync_jky_procurement_tracking",
    soft_time_limit=1200,
    time_limit=1500,
)
def sync_jky_procurement_tracking() -> dict[str, Any]:
    """只跟踪吉客云采购入库，不触发销售导出和全量库存同步。

    设计目标：
    - 供 1688 → 吉客云采购入库单号备注匹配使用；
    - 允许 JACKYUN_SYNC_MODE=manual 时仍做这一条轻量采购链跟踪；
    - 登录态失效时明确返回 need_login / need_user_verify，不伪装成功；
    - 每次抓完入库单后立刻重新核验 1688 备注并自动建链。
    """
    db = SessionLocal()
    job = None
    try:
        job = start_sync_job(db, PROVIDER, "procurement_inbound_tracking")
        manager = JkyWebSessionManager(db)
        try:
            auth = manager.load_auth()
            client = JkyWebClient(auth)
        except JkyWebError as exc:
            status = "need_login" if exc.kind == "need_login" else "failed"
            if exc.kind == "need_user_verify":
                status = "need_user_verify"
            connection_status = (
                SESSION_NEED_LOGIN if status == "need_login"
                else SESSION_NEED_VERIFY if status == "need_user_verify"
                else SESSION_ERROR
            )
            manager.set_status(connection_status, str(exc)[:500])
            finish_sync_job(db, job, status, {}, str(exc)[:500])
            db.commit()
            return {"status": status, "error": str(exc)[:500]}

        end = datetime.now()
        start = end - timedelta(days=max(int(settings.JKY_WEB_SYNC_LOOKBACK_DAYS), 1))
        try:
            stockin = sync_stockin(db, client, start, end)
            remark_match = run_verified_remark_match(db, actor="system")
            manager.set_status(SESSION_ACTIVE)
            stats = {
                "stockin": stockin,
                "remarkMatch": remark_match,
                "window": {"start": start.isoformat(), "end": end.isoformat()},
            }
            finish_sync_job(db, job, "success", stats, "")
            db.add(SyncLog(
                provider=PROVIDER,
                level="info",
                sync_job_id=job.id,
                message="吉客云采购入库跟踪完成",
                data={"stats": stats},
            ))
            db.commit()
            return {"status": "success", "stats": stats}
        except JkyWebError as exc:
            db.rollback()
            status = "failed"
            connection_status = SESSION_ERROR
            if exc.kind == "need_login":
                status = "need_login"
                connection_status = SESSION_NEED_LOGIN
            elif exc.kind == "need_user_verify":
                status = "need_user_verify"
                connection_status = SESSION_NEED_VERIFY
            manager.set_status(connection_status, str(exc)[:500])
            finish_sync_job(db, job, status, {}, str(exc)[:500])
            db.commit()
            return {"status": status, "error": str(exc)[:500]}
    finally:
        db.close()
