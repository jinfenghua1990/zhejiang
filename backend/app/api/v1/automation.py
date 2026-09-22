from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.core.audit import audit
from app.config import settings
from app.db import get_db
from app.models.integration import SyncJob, SyncLog

router = APIRouter(prefix="/automation", tags=["automation"])

_order_interval = max(1, min(settings.JKY_ORDER_SYNC_INTERVAL_MINUTES, 59))
_manual = settings.JACKYUN_SYNC_MODE == "manual"
_order_frequency = (
    "已停用（JACKYUN_SYNC_MODE=manual，改手动上传）"
    if _manual
    else f"每 {_order_interval} 分钟"
)
_jky_frequency = (
    "已停用（JACKYUN_SYNC_MODE=manual，改手动上传）"
    if _manual
    else "每 15 分钟（错峰）"
)
_daily_jky_frequency = (
    "已停用（JACKYUN_SYNC_MODE=manual，改手动上传）"
    if _manual
    else "每天定时"
)
SCHEDULE = [
    {"task": "tasks.sync_jky_orders", "args": "", "label": "吉客云 销售订单（三通道自动切换）", "frequency": _order_frequency},
    {"task": "tasks.sync_jackyun", "args": "aftersales", "label": "吉客云 售后", "frequency": _jky_frequency},
    {"task": "tasks.sync_jackyun", "args": "inventory", "label": "吉客云 库存", "frequency": _jky_frequency},
    {"task": "tasks.sync_jackyun", "args": "products", "label": "吉客云 商品/SKU", "frequency": _daily_jky_frequency},
    {"task": "tasks.sync_jackyun", "args": "price_lists", "label": "吉客云 SKU/价格", "frequency": _daily_jky_frequency},
    {"task": "tasks.sync_jackyun", "args": "warehouses", "label": "吉客云 仓库", "frequency": _daily_jky_frequency},
    {"task": "tasks.sync_jackyun", "args": "purchase", "label": "吉客云 采购", "frequency": _jky_frequency},
    {"task": "tasks.sync_jackyun", "args": "online_orders", "label": "吉客云 OMS 订单", "frequency": _jky_frequency},
    {"task": "tasks.sync_jackyun", "args": "shop_orders", "label": "吉客云 网店订单/发货", "frequency": _jky_frequency},
    {"task": "tasks.sync_jackyun", "args": "purchase_settlements", "label": "吉客云 采购结算", "frequency": _jky_frequency},
    {"task": "tasks.sync_jackyun", "args": "purchase_returns", "label": "吉客云 采购退货", "frequency": _jky_frequency},
    {"task": "tasks.sync_jackyun", "args": "stock_allocations", "label": "吉客云 库存调拨", "frequency": _jky_frequency},
    {"task": "tasks.sync_jackyun", "args": "inbound", "label": "吉客云 入库单", "frequency": _daily_jky_frequency},
    {"task": "tasks.sync_jackyun", "args": "outbound", "label": "吉客云 出库单", "frequency": _daily_jky_frequency},
    {"task": "tasks.sync_jky_web", "args": "", "label": "吉客云 档案（商品/SKU/仓库）", "frequency": _daily_jky_frequency},
    {"task": "tasks.sync_1688", "args": "", "label": "1688 订单", "frequency": "已停用定时（改采购执行中心手动采集）"},
    {"task": "tasks.generate_monthly_sales_outbound", "args": "", "label": "财务 销售出库底稿", "frequency": "每月 2 日 04:10"},
    {"task": "tasks.generate_monthly_finance_sales_report", "args": "", "label": "财务 销售汇总", "frequency": "每月 2 日 04:20"},
    {"task": "tasks.generate_monthly_accounting_summary", "args": "", "label": "财务 销售开票分类汇总", "frequency": "每月 2 日 04:30"},
    {"task": "tasks.monthly_verify", "args": "", "label": "月初完整校验", "frequency": "每月 3 日 06:00"},
    {"task": "tasks.finance_auto_delivery", "args": "", "label": "财务 自动发送检查", "frequency": "每小时 25 分检查；按月结设置的发送日/小时执行"},
]

JACKYUN_JOB_TYPES = {
    "sales", "online_orders", "aftersales", "inventory", "products", "price_lists",
    "warehouses", "purchase", "purchase_settlements", "purchase_returns", "inbound", "outbound",
    "stock_allocations", "shop_orders",
}


@router.post("/run/jackyun/{job_type}")
def run_jackyun(job_type: str, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    if job_type not in JACKYUN_JOB_TYPES:
        raise HTTPException(400, f"未知吉客云同步类型: {job_type}")
    if job_type == "sales":
        from app.tasks.sync import sync_jky_orders

        task = sync_jky_orders.delay()
        audit(db, current_actor(request), "automation.jky_order.queued", "celery_task", task.id)
        return {"ok": True, "taskId": task.id, "jobType": "orders", "status": "queued"}

    from app.tasks.sync import sync_jackyun

    task = sync_jackyun.delay(job_type, True)
    audit(db, current_actor(request), "automation.jackyun.queued", "celery_task", task.id,
          {"jobType": job_type})
    return {"ok": True, "taskId": task.id, "jobType": job_type, "status": "queued"}


@router.post("/run/1688")
def run_1688(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.tasks.sync import sync_1688

    task = sync_1688.delay()
    audit(db, current_actor(request), "automation.1688.queued", "celery_task", task.id)
    return {"ok": True, "taskId": task.id, "status": "queued"}


@router.get("/schedule")
def schedule() -> dict[str, Any]:
    return {
        "items": SCHEDULE,
        "note": "财务月度主表按官方开票的财务大类+税率汇总；底层商品/SKU明细仅保留在系统内，不作为默认财务发送主表。",
    }


@router.get("/jobs")
def jobs(limit: int = 50, provider: str | None = None,
         db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    q = db.query(SyncJob).order_by(SyncJob.id.desc())
    if provider:
        q = q.filter(SyncJob.provider == provider)
    return [
        {
            "id": j.id, "provider": j.provider, "jobType": j.job_type,
            "status": j.status, "startedAt": j.started_at.isoformat() if j.started_at else None,
            "finishedAt": j.finished_at.isoformat() if j.finished_at else None,
            "stats": j.stats or {}, "errorSummary": j.error_summary or "",
        }
        for j in q.limit(min(max(limit, 1), 200)).all()
    ]


@router.get("/logs")
def logs(limit: int = 100, provider: str | None = None,
         db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    q = db.query(SyncLog).order_by(SyncLog.id.desc())
    if provider:
        q = q.filter(SyncLog.provider == provider)
    return [
        {
            "id": r.id, "provider": r.provider, "level": r.level,
            "message": r.message, "jobId": r.sync_job_id,
        }
        for r in q.limit(min(max(limit, 1), 300)).all()
    ]
