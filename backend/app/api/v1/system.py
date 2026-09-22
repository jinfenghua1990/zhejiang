from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.config import settings
from app.core.logging import get_logger
from app.db import get_db
from app.api.deps import current_actor, require_roles
from app.models.integration import SyncJob
from app.models.ops import ExceptionRecord
from app.services import integration_service

router = APIRouter(prefix="/system", tags=["system"])
_log = get_logger("system.health")

class SystemUpdateSettingsBody(BaseModel):
    enabled: bool | None = None
    mode: str | None = None
    checkIntervalMinutes: int | None = Field(None, ge=5, le=1440)
    autoUpdateHour: int | None = Field(None, ge=0, le=23)
    autoUpdateWindowMinutes: int | None = Field(None, ge=15, le=360)
    autoInstallLevel: str | None = None




def _runtime_release_info(db: Session) -> dict[str, Any]:
    """Return the version identity of the process that is actually serving traffic."""
    revision = ""
    try:
        revision = str(db.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar() or "")
    except Exception as exc:
        _log.warning("alembic revision probe failed: %s", exc)

    image_ref = (settings.APP_IMAGE_REF or "").strip()
    image_tag = ""
    if image_ref:
        tail = image_ref.rsplit("/", 1)[-1]
        if ":" in tail:
            image_tag = tail.rsplit(":", 1)[-1]

    git_sha = (settings.GIT_SHA or "").strip()
    version = image_tag or (f"sha-{git_sha[:12]}" if git_sha else "local")
    container_managed = settings.DEPLOYMENT_MODE == "container"
    return {
        "appEnv": settings.APP_ENV,
        "releaseChannel": settings.RELEASE_CHANNEL,
        "deploymentMode": settings.DEPLOYMENT_MODE,
        "managedBy": "github_ghcr" if container_managed else "git_native",
        "gitSha": git_sha,
        "imageRef": image_ref,
        "imageTag": image_tag,
        "version": version,
        "alembicRevision": revision,
        "inAppUpdateEnabled": bool(settings.SYSTEM_UPDATE_ENABLED and not container_managed),
    }

_PROVIDER_LABELS = {
    "jackyun": "吉客云",
    "jky_order": "吉客云订单",
    "jky_procurement": "吉客云采购",
    "jky_web": "吉客云档案",
    "jackyun_files": "吉客云文件",
    "alibaba_1688": "1688 采购",
    "alibaba1688": "1688 采购",
    "alibaba1688.browser": "1688 同步",
    "sales_outbound": "销售出库",
}


@router.get("/update/status", dependencies=[Depends(require_roles("admin"))])
def system_update_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.services import system_update_service
    payload = system_update_service.status_payload(include_log=True)
    payload["runtime"] = _runtime_release_info(db)
    if settings.DEPLOYMENT_MODE == "container" and settings.GIT_SHA:
        payload["currentSha"] = settings.GIT_SHA
    return payload


@router.get("/update/readiness", dependencies=[Depends(require_roles("admin"))])
def system_update_readiness(db: Session = Depends(get_db)) -> dict[str, Any]:
    if settings.DEPLOYMENT_MODE == "container":
        runtime = _runtime_release_info(db)
        checks = [
            {
                "key": "container_image",
                "label": "运行镜像",
                "status": "ok" if runtime["imageRef"] else "warn",
                "detail": runtime["imageRef"] or "未注入 APP_IMAGE_REF",
                "blocking": False,
            },
            {
                "key": "git_sha",
                "label": "Git Revision",
                "status": "ok" if runtime["gitSha"] else "warn",
                "detail": runtime["gitSha"] or "镜像未写入 GIT_SHA",
                "blocking": False,
            },
            {
                "key": "database_revision",
                "label": "数据库版本",
                "status": "ok" if runtime["alembicRevision"] else "warn",
                "detail": runtime["alembicRevision"] or "无法读取 alembic_version",
                "blocking": False,
            },
        ]
        return {
            "ready": True,
            "checks": checks,
            "blockingCount": 0,
            "warningCount": sum(1 for item in checks if item["status"] == "warn"),
            "branch": "",
            "remote": "",
            "checkedAt": datetime.now(ZoneInfo(settings.TZ)).isoformat(),
        }
    from app.services import system_update_service
    return system_update_service.update_readiness()


@router.post("/update/check", dependencies=[Depends(require_roles("admin"))])
def system_update_check(request: Request) -> dict[str, Any]:
    if settings.DEPLOYMENT_MODE == "container":
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="容器模式由 GitHub / GHCR 管理版本，请通过发布流程检查候选版本。")
    from app.services import system_update_service
    return system_update_service.check_for_updates(actor=current_actor(request), automatic=False)


@router.get("/update/local-changes", dependencies=[Depends(require_roles("admin"))])
def system_update_local_changes(include_diff: bool = Query(False)) -> dict[str, Any]:
    if settings.DEPLOYMENT_MODE == "container":
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="容器模式没有可上传的本地 Git 工作区修改。")
    from app.services import system_update_service
    try:
        return system_update_service.local_changes_payload(include_diff=include_diff)
    except (ValueError, RuntimeError) as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/update/local-changes/upload", dependencies=[Depends(require_roles("admin"))])
def system_update_upload_local_changes(request: Request) -> dict[str, Any]:
    if settings.DEPLOYMENT_MODE == "container":
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="容器模式禁止从运行实例上传本地代码修改。")
    from app.services import system_update_service
    try:
        return system_update_service.upload_local_changes(actor=current_actor(request))
    except (ValueError, RuntimeError) as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/update/local-changes/sync", dependencies=[Depends(require_roles("admin"))])
def system_update_sync_local_changes(request: Request) -> dict[str, Any]:
    if settings.DEPLOYMENT_MODE == "container":
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="容器模式禁止从运行实例同步本地代码。")
    from app.services import system_update_service
    try:
        return system_update_service.sync_local_changes(actor=current_actor(request))
    except (ValueError, RuntimeError) as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/update/settings", dependencies=[Depends(require_roles("admin"))])
def system_update_settings(body: SystemUpdateSettingsBody) -> dict[str, Any]:
    from app.services import system_update_service
    try:
        saved = system_update_service.save_update_settings(body.model_dump(exclude_none=True))
    except ValueError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "settings": saved}


@router.post("/update/apply", dependencies=[Depends(require_roles("admin"))])
def system_update_apply(request: Request) -> dict[str, Any]:
    if settings.DEPLOYMENT_MODE == "container":
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="容器模式禁止应用内更新；请发布已验证的 GHCR 镜像。")
    from app.services import system_update_service
    try:
        return system_update_service.start_update(actor=current_actor(request))
    except (ValueError, RuntimeError) as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/global-status")
def global_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    """顶部全局状态：待处理异常数 + 各数据源最近同步情况。"""
    pending = (
        db.query(func.count(ExceptionRecord.id))
        .filter(ExceptionRecord.status == "pending")
        .scalar()
    ) or 0

    jobs = db.query(SyncJob).order_by(SyncJob.id.desc()).limit(120).all()
    sources: dict[str, dict[str, Any]] = {}
    for job in jobs:
        if job.provider in sources:
            continue
        finished = job.finished_at or job.started_at
        sources[job.provider] = {
            "provider": job.provider,
            "label": _PROVIDER_LABELS.get(job.provider, job.provider),
            "status": job.status,
            "lastAt": finished.isoformat() if finished else None,
        }
    ordered = sorted(sources.values(), key=lambda item: item["lastAt"] or "", reverse=True)
    running = any(item["status"] == "running" for item in ordered)
    failed = any(item["status"] == "failed" for item in ordered)
    last_sync_at = next((item["lastAt"] for item in ordered if item["lastAt"]), None)
    return {
        "pendingExceptions": int(pending),
        "lastSyncAt": last_sync_at,
        "state": "running" if running else ("failed" if failed else ("ok" if ordered else "empty")),
        "sources": ordered[:8],
    }


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, Any]:
    components = {"database": "down", "redis": "down"}
    try:
        db.execute(text("SELECT 1"))
        components["database"] = "up"
    except Exception as exc:
        _log.warning("database health probe failed: %s", exc)
    try:
        import redis as redis_lib

        from app.config import settings as s

        r = redis_lib.Redis.from_url(s.REDIS_URL, socket_connect_timeout=2)
        components["redis"] = "up" if r.ping() else "down"
    except Exception as exc:
        _log.warning("redis health probe failed: %s", exc)
    status = "ready" if all(v == "up" for v in components.values()) else "degraded"
    return {"status": status, "components": components}


@router.get("/overview")
def overview(
    period_year: int | None = Query(None, ge=2000, le=2100),
    period_month: int | None = Query(None, ge=1, le=12),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """经营总览只展示一个自然月；缺省为当前业务时区月份，不再返回全历史累计。"""
    from app.config import settings
    from app.models.ops import ExceptionRecord
    from app.services import monthly_core, profit as profit_service

    now = datetime.now(ZoneInfo(settings.TZ))
    year = period_year or now.year
    month = period_month or now.month

    metrics = monthly_core.sales_overview(db, year, month)
    recon = monthly_core.reconciliation_overview(db, year, month)
    profit = profit_service.compute(db, year, month)
    metrics["grossProfit"] = profit.get("grossProfit")
    metrics["receivable"] = recon.get("receivable")
    metrics["received"] = recon.get("received")
    metrics["pendingReceive"] = recon.get("pending")

    pending_exceptions = (
        db.query(ExceptionRecord).filter(ExceptionRecord.status == "pending").count()
    )
    return {
        "phase": 6,
        "phaseName": "Phase 6 期初+异常+月结",
        "period": f"{year}-{month:02d}",
        "accessMode": settings.ACCESS_MODE,
        "dataState": "partial",
        "integrations": integration_service.integration_status(db),
        "pendingExceptions": pending_exceptions,
        "nextMilestone": "供应链月度经营闭环与月结口径统一",
        "metrics": metrics,
    }
