from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.api.deps import current_actor, require_roles
from app.services import (
    backup_status_service,
    integration_service,
    kodo_backup_service,
    r2_backup_service,
    webdav_backup_service,
)

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("")
def list_integrations(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return integration_service.integration_status(db)


class KodoColdBackupConfigIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    bucket: str
    upload_url: str = Field(alias="uploadUrl")
    prefix: str = "ecommerce-workspace/cold"
    access_key: str = Field(default="", alias="accessKey")
    secret_key: str = Field(default="", alias="secretKey")
    enabled: bool = True


@router.get("/kodo-cold", dependencies=[Depends(require_roles("admin"))])
def get_kodo_cold_backup_config(db: Session = Depends(get_db)) -> dict[str, Any]:
    """只返回脱敏后的 Kodo 冷备配置；密钥永不回传前端。"""
    return kodo_backup_service.get_config(db)


@router.put("/kodo-cold", dependencies=[Depends(require_roles("admin"))])
def save_kodo_cold_backup_config(
    body: KodoColdBackupConfigIn,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """保存 Kodo upload-only 配置。该通道不提供任何读取/下载/恢复接口。"""
    try:
        return kodo_backup_service.save_config(
            db,
            bucket=body.bucket,
            upload_url=body.upload_url,
            prefix=body.prefix,
            access_key=body.access_key,
            secret_key=body.secret_key,
            enabled=body.enabled,
            actor=current_actor(request),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/kodo-cold/test-write", dependencies=[Depends(require_roles("admin"))])
def test_kodo_cold_backup_write(db: Session = Depends(get_db)) -> dict[str, Any]:
    """只执行一次 Kodo 上传探针；不读取、不列目录、不删除远端对象。"""
    try:
        return kodo_backup_service.test_write_connection(db)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/kodo-cold/run", dependencies=[Depends(require_roles("admin"))])
def run_kodo_cold_backup(db: Session = Depends(get_db)) -> dict[str, Any]:
    """手动启动一次完整冷备；上传仍保持 upload-only。"""
    config = kodo_backup_service.get_config(db)
    if not config["configured"] or not config["enabled"]:
        raise HTTPException(400, "Kodo 冷备尚未配置或当前未启用")
    try:
        return kodo_backup_service.start_backup()
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


class WebdavBackupConfigIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    base_url: str = Field(default=webdav_backup_service.DEFAULT_BASE_URL, alias="baseUrl")
    remote_path: str = Field(default=webdav_backup_service.DEFAULT_PREFIX, alias="remotePath")
    username: str
    app_password: str = Field(default="", alias="appPassword")
    enabled: bool = True


@router.get("/webdav-backup", dependencies=[Depends(require_roles("admin"))])
def get_webdav_backup_config(db: Session = Depends(get_db)) -> dict[str, Any]:
    """返回脱敏后的坚果云 WebDAV 配置；应用密码永不回传前端。"""
    return webdav_backup_service.get_config(db)


@router.put("/webdav-backup", dependencies=[Depends(require_roles("admin"))])
def save_webdav_backup_config(
    body: WebdavBackupConfigIn,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """保存坚果云 WebDAV 备份配置，凭据加密落库。"""
    try:
        return webdav_backup_service.save_config(
            db,
            base_url=body.base_url,
            remote_path=body.remote_path,
            username=body.username,
            app_password=body.app_password,
            enabled=body.enabled,
            actor=current_actor(request),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/webdav-backup/test", dependencies=[Depends(require_roles("admin"))])
def test_webdav_backup_connection(db: Session = Depends(get_db)) -> dict[str, Any]:
    """验证 WebDAV 登录、写入、读取和清理探针，不触碰业务备份文件。"""
    try:
        return webdav_backup_service.test_connection(db)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/webdav-backup/run", dependencies=[Depends(require_roles("admin"))])
def run_webdav_backup(db: Session = Depends(get_db)) -> dict[str, Any]:
    """手动启动一次 WebDAV 完整容灾备份。"""
    try:
        return webdav_backup_service.start_backup(db)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


class R2BackupConfigIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    endpoint_url: str = Field(alias="endpointUrl")
    bucket: str
    prefix: str = "ecommerce-workspace/backup"
    access_key: str = Field(default="", alias="accessKey")
    secret_key: str = Field(default="", alias="secretKey")
    enabled: bool = True
    full_interval_days: int = Field(default=10, alias="fullIntervalDays")


class R2BackupRunIn(BaseModel):
    mode: str = "auto"


class R2RestorePrepareIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    snapshot_object_key: str = Field(default="", alias="snapshotObjectKey")


@router.get("/r2-backup", dependencies=[Depends(require_roles("admin"))])
def get_r2_backup_config(db: Session = Depends(get_db)) -> dict[str, Any]:
    """返回脱敏后的 Cloudflare R2 主备份配置。"""
    return r2_backup_service.get_config(db)


@router.put("/r2-backup", dependencies=[Depends(require_roles("admin"))])
def save_r2_backup_config(
    body: R2BackupConfigIn,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """保存 R2 主备份配置；密钥加密落库且永不回传。"""
    try:
        return r2_backup_service.save_config(
            db,
            endpoint_url=body.endpoint_url,
            bucket=body.bucket,
            prefix=body.prefix,
            access_key=body.access_key,
            secret_key=body.secret_key,
            enabled=body.enabled,
            full_interval_days=body.full_interval_days,
            actor=current_actor(request),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/r2-backup/test", dependencies=[Depends(require_roles("admin"))])
def test_r2_backup_connection(db: Session = Depends(get_db)) -> dict[str, Any]:
    """非破坏性测试 R2 主备连接；仅执行签名 HEAD Bucket。"""
    try:
        return r2_backup_service.test_connection(db)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/r2-backup/run", dependencies=[Depends(require_roles("admin"))])
def run_r2_backup(body: R2BackupRunIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    """启动 R2 备份任务。auto=每日模块化，并按配置间隔自动插入全量容灾点。"""
    config = r2_backup_service.get_config(db)
    if not config["configured"] or not config["enabled"]:
        raise HTTPException(400, "R2 尚未配置或当前未启用")
    try:
        return r2_backup_service.start_backup(body.mode)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/backup-status", dependencies=[Depends(require_roles("admin"))])
def get_backup_status(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    """读取本地 manifest / R2 / Kodo 成功回执，作为备份中心真实执行记录。"""
    return backup_status_service.get_status(limit=limit)


@router.post("/r2-backup/prepare-restore", dependencies=[Depends(require_roles("admin"))])
def prepare_r2_restore(
    body: R2RestorePrepareIn | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """下载并校验指定或最新 R2 全量恢复点到 staging；绝不自动覆盖生产环境。"""
    config = r2_backup_service.get_config(db)
    if not config["configured"] or not config["enabled"]:
        raise HTTPException(400, "R2 尚未配置或当前未启用")
    try:
        return r2_backup_service.start_restore_prepare(
            body.snapshot_object_key if body else "",
            prefix=str(config.get("prefix") or r2_backup_service.DEFAULT_PREFIX),
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/jackyun/test")
def test_jackyun(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """真实 MCP 连接测试：initialize → tools/list。结果如实返回。"""
    return integration_service.test_jackyun(db, actor=current_actor(request))


# ---------- 1688 OAuth（规格 7.2：先完成授权页面/callback 骨架；未配置如实显示等待） ----------

@router.get("/alibaba1688/auth-url")
def alibaba1688_auth_url() -> dict[str, Any]:
    """生成 1688 授权跳转地址。未配置 AppKey/Secret 时明确报错，不伪造已连接。"""
    from uuid import uuid4

    from app.adapters.alibaba1688 import Alibaba1688Adapter
    from app.adapters.base import AdapterNotConfigured

    try:
        adapter = Alibaba1688Adapter()
        url = adapter.get_authorization_url(state=str(uuid4()))
    except AdapterNotConfigured as exc:
        raise HTTPException(400, str(exc))
    except NotImplementedError as exc:
        raise HTTPException(400, str(exc))
    return {"authorizationUrl": url}


@router.get("/alibaba1688/callback")
def alibaba1688_callback(
    code: str = Query(...),
    state: str = Query(""),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """1688 授权回调：token 交换需 AppKey/Secret 配置后实现；未配置时记录并如实返回等待状态。"""
    from app.adapters.alibaba1688 import Alibaba1688Adapter
    from app.adapters.base import AdapterNotConfigured
    from app.services.integration_service import ensure_exception

    try:
        adapter = Alibaba1688Adapter()
        result = adapter.handle_callback(code, state)
        return {"ok": True, **result}
    except AdapterNotConfigured as exc:
        ensure_exception(db, "ALIBABA1688_NOT_CONFIGURED", "1688 未配置",
                         f"回调已收到但未配置 AppKey/Secret: {exc}")
        return {"ok": False, "status": "waiting_config",
                "message": "回调已收到；1688 开放平台应用创建并配置 AppKey/Secret 后完成授权"}
    except NotImplementedError as exc:
        return {"ok": False, "status": "token_exchange_pending",
                "message": str(exc) or "token 交换待 1688 应用权限确认后实现"}
