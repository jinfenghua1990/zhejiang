from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.jackyun import JackyunAdapter, finish_sync_job, start_sync_job
from app.adapters.base import AdapterError, AdapterNotConfigured
from app.config import settings
from app.core.audit import audit
from app.models.integration import IntegrationConnection
from app.models.ops import ExceptionRecord

PROVIDERS = [
    {"id": "jackyun_mcp", "name": "吉客云 MCP", "mode": "服务端 Token", "phase": 1},
    {"id": "jackyun_openapi", "name": "吉客 OpenAPI", "mode": "服务端 AppKey/AppSecret", "phase": 1},
    {"id": "jky_web", "name": "吉客云 Web 连接器", "mode": "网页登录态", "phase": 1},
    {"id": "jky_rpa", "name": "吉客云 Windows RPA", "mode": "订单同步", "phase": 1},
    {"id": "jky_api", "name": "吉客云 OpenAPI/MCP（订单备用）", "mode": "订单同步", "phase": 1},
    {"id": "alibaba_1688", "name": "1688 买家订单", "mode": "浏览器直采", "phase": 4},
    {"id": "zhejiang_rural_credit", "name": "浙江农信", "mode": "Excel/PDF 文件导入", "phase": 3},
    {"id": "smtp", "name": "财务邮件", "mode": "人工确认后 SMTP 发送", "phase": 6},
]


def get_or_create_connection(db: Session, provider: str, mode: str = "", phase: int = 1) -> IntegrationConnection:
    row = db.query(IntegrationConnection).filter_by(provider=provider, mode=mode).first()
    if not row:
        row = IntegrationConnection(provider=provider, mode=mode, phase=phase)
        db.add(row)
        db.commit()
    return row


def integration_status(db: Session) -> list[dict[str, Any]]:
    """页面刷新只读本地库，不触发外部查询（规格 5）。"""
    jackyun_ready = settings.jackyun_configured
    alibaba_ready = settings.alibaba_1688_configured
    smtp_ready = settings.smtp_configured
    rows: list[dict[str, Any]] = []
    # 状态页不能因为“查看一次”就创建连接行、提交事务；创建只发生在真实测试/同步动作中。
    connections = {
        (row.provider, row.mode): row
        for row in db.query(IntegrationConnection).all()
    }
    for p in PROVIDERS:
        pid = p["id"]
        conn = connections.get((pid, p["mode"]))
        status = conn.status if conn else "unconfigured"
        if pid == "jackyun_mcp":
            if not jackyun_ready:
                status = "unconfigured"
            elif conn and conn.status in ("connected", "transport_connected", "blocked", "error"):
                status = conn.status
            else:
                status = "untested"
        elif pid == "jackyun_openapi":
            status = "unconfigured"
        elif pid == "jky_web":
            # V1 主通道：网页登录态。状态由 SessionManager 状态机维护，
            # needs_login / needs_user_verify 均如实透出，不伪装已连接。
            if conn and conn.status in ("connected", "needs_login", "needs_user_verify", "error"):
                status = conn.status
            else:
                status = "untested"
        elif pid in ("jky_rpa", "jky_api"):
            if pid == "jky_rpa":
                configured = bool(settings.JKY_RPA_AGENT_URL and settings.JKY_RPA_AGENT_TOKEN)
            else:
                configured = settings.jackyun_configured
            if not configured:
                status = "unconfigured"
            elif conn and conn.status in ("connected", "blocked", "error", "needs_login", "needs_user_verify"):
                status = conn.status
            else:
                status = "configured_not_tested"
        elif pid == "alibaba_1688":
            # 主通道 = 浏览器直采（登录态在持久化 Profile 里，无凭证配置概念）；
            # 开放平台 OAuth 保留为备用：浏览器通道关闭且 OAuth 已配置时走旧逻辑。
            if settings.ALIBABA_1688_BROWSER_ENABLED:
                if conn and conn.status in ("connected", "error", "needs_login"):
                    status = conn.status
                else:
                    status = "untested"
            elif not alibaba_ready:
                status = "unconfigured"
            elif conn and conn.status in ("connected", "error"):
                status = conn.status
            else:
                status = "untested"
        elif pid == "zhejiang_rural_credit":
            status = "available"  # 文件导入模式天然可用，无需凭证
        elif pid == "smtp":
            if not smtp_ready:
                status = "unconfigured"
            elif conn and conn.status in ("connected", "error"):
                status = conn.status
            else:
                status = "untested"
        rows.append({
            "id": pid,
            "name": p["name"],
            "mode": p["mode"],
            "phase": p["phase"],
            "status": status,
            "lastTestedAt": conn.last_tested_at.isoformat() if conn and conn.last_tested_at else None,
            "lastSuccessAt": conn.last_success_at.isoformat() if conn and conn.last_success_at else None,
            "errorSummary": (conn.error_summary or None) if conn else None,
        })
    return rows


def test_jackyun(db: Session, actor: str = "system") -> dict[str, Any]:
    """真实调用 MCP：initialize → tools/list。

    此检查只能证明传输层连通，不能把它冒充成已验证的业务 API 权限。
    业务状态只由实际同步成功（connected）或权限拒绝（blocked）更新。
    """
    conn = get_or_create_connection(db, "jackyun_mcp", "服务端 Token", 1)
    conn.last_tested_at = datetime.now(timezone.utc)
    job = start_sync_job(db, "jackyun", "connection_test")
    try:
        adapter = JackyunAdapter(db)
        result = adapter.test_connection()
        if conn.status not in ("connected", "blocked"):
            conn.status = "transport_connected"
        business_ready = conn.status == "connected"
        conn.last_success_at = datetime.now(timezone.utc)
        if conn.status != "blocked":
            conn.error_summary = ""
        conn.meta = {
            **(conn.meta or {}),
            "tools": result["tools"],
            "serverInfo": result["server_info"],
            "transportConnected": True,
            "businessApiAvailable": business_ready,
        }
        finish_sync_job(db, job, "success", {"tools_count": len(result["tools"])})
        audit(db, actor, "jackyun.connection_test.success", "integration", conn.id,
              {"tools": result["tools"]})
        return {
            "ok": True,
            "status": conn.status,
            "businessReady": business_ready,
            **result,
        }
    except AdapterNotConfigured as exc:
        msg = str(exc)
        conn.status = "unconfigured"
        conn.error_summary = ""
        finish_sync_job(db, job, "skipped", {}, msg)
        db.add(SyncLogRow(provider="jackyun", level="warn", message=msg, sync_job_id=job.id))
        db.commit()
        audit(db, actor, "jackyun.connection_test.skipped", "integration", conn.id, {"reason": msg})
        return {"ok": False, "status": "unconfigured", "error": msg}
    except (AdapterError, NotImplementedError) as exc:
        msg = str(exc)
        conn.status = "error"
        conn.error_summary = msg[:500]
        finish_sync_job(db, job, "failed", {}, msg)
        db.add(SyncLogRow(provider="jackyun", level="error", message=msg, sync_job_id=job.id))
        ensure_exception(db, "JACKYUN_SYNC_FAIL", "吉客云同步失败", msg)
        db.commit()
        audit(db, actor, "jackyun.connection_test.failed", "integration", conn.id, {"error": msg})
        return {"ok": False, "error": msg}


# 避免循环导入的小包装
def SyncLogRow(**kw):  # noqa: N802
    from app.models.integration import SyncLog

    return SyncLog(**kw)


def ensure_exception(db: Session, code: str, title: str, detail: str) -> None:
    exists = (
        db.query(ExceptionRecord)
        .filter(ExceptionRecord.code == code, ExceptionRecord.status.in_(("pending", "confirmed")))
        .first()
    )
    if exists:
        exists.detail = {"latest": detail[:2000]}
    else:
        db.add(ExceptionRecord(code=code, type=code, title=title, detail={"latest": detail[:2000]},
                               severity="high", source="system"))
    db.commit()


def resolve_exception(db: Session, code: str, note: str) -> int:
    """故障恢复后自动关闭同 code 的未处理异常（ensure_exception 的对称操作）。

    只关闭 pending（人工 confirmed 的不动，handled_by 记 system 便于追溯）。
    返回关闭条数；供各同步成功路径调用，避免历史故障告警永远挂着。
    """
    from datetime import datetime as _dt, timezone as _tz

    rows = (
        db.query(ExceptionRecord)
        .filter(ExceptionRecord.code == code, ExceptionRecord.status == "pending")
        .all()
    )
    for row in rows:
        row.status = "resolved"
        row.handled_by = "system"
        row.handled_at = _dt.now(_tz.utc)
        row.note = note[:500]
    if rows:
        db.commit()
    return len(rows)
