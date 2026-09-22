from __future__ import annotations

from typing import Any

from app.celery_app import celery_app
from app.db import SessionLocal
from app.adapters.jackyun import JackyunAdapter, finish_sync_job, start_sync_job
from app.adapters.base import AdapterNotConfigured, AdapterPermissionError
from app.config import settings
from app.models.integration import IntegrationConnection, SyncLog
from app.services.integration_service import ensure_exception, get_or_create_connection


def _set_jackyun_connection(db, status: str, message: str = "") -> None:
    from datetime import datetime, timezone

    conn = get_or_create_connection(db, "jackyun_mcp", "服务端 Token", 1)
    conn.status = status
    conn.error_summary = message[:500]
    meta = dict(conn.meta or {})
    meta["businessApiAvailable"] = status == "connected"
    conn.meta = meta
    if status == "connected":
        conn.last_success_at = datetime.now(timezone.utc)
    db.commit()


def _finish_attempt(db, job_id: int, status: str, message: str, stats: dict | None = None) -> None:
    """结束当前尝试；不再额外创建一条重复 SyncJob。"""
    from app.models.integration import SyncJob, SyncLog

    db.rollback()
    job = db.get(SyncJob, job_id)
    if job is None:
        return
    finish_sync_job(db, job, status, stats or {}, message if status != "success" else "")
    db.add(
        SyncLog(
            provider="jackyun",
            level="info" if status == "success" else "error",
            message=message,
            sync_job_id=job.id,
        )
    )
    db.commit()


def _record(provider: str, job_type: str, status: str, message: str, stats: dict | None = None) -> None:
    db = SessionLocal()
    try:
        job = start_sync_job(db, provider, job_type)
        finish_sync_job(db, job, status, stats or {}, message if status != "success" else "")
        from app.models.integration import SyncLog

        db.add(SyncLog(provider=provider, level="info" if status == "success" else "warn",
                       message=message, sync_job_id=job.id))
        db.commit()
    finally:
        db.close()


def _automatic_jackyun_sync_paused(db) -> bool:
    """业务权限已明确阻断后，定时器不应继续高频撞接口；手动操作仍可强制重试。"""
    conn = (
        db.query(IntegrationConnection)
        .filter_by(provider="jackyun_mcp", mode="服务端 Token")
        .first()
    )
    return bool(conn and conn.status == "blocked")


@celery_app.task(name="tasks.sync_jackyun", bind=True, max_retries=3, default_retry_delay=60)
def sync_jackyun(self, job_type: str, force: bool = False) -> dict[str, Any]:
    """吉客云定时同步。未配置→跳过；所有已确认工具均落原始副本与本地业务表。"""
    db = SessionLocal()
    try:
        if not settings.jackyun_configured:
            return {"status": "skipped", "reason": "unconfigured"}
        if not force and _automatic_jackyun_sync_paused(db):
            return {
                "status": "blocked_skip",
                "reason": "业务权限未开通，已暂停定时重试；开通后请从自动化页手动立即同步",
            }
        adapter = JackyunAdapter(db)
        try:
            adapter.ensure_configured()
        except AdapterNotConfigured as exc:
            return {"status": "skipped", "reason": str(exc)}

        method_map = {
            "sales": "sync_sales_orders",
            "online_orders": "sync_online_orders",
            "aftersales": "sync_aftersales",
            "inventory": "sync_inventory",
            "products": "sync_products",
            "price_lists": "sync_price_lists",
            "warehouses": "sync_warehouses",
            "purchase": "sync_purchase_orders",
            "purchase_settlements": "sync_purchase_settlements",
            "purchase_returns": "sync_purchase_returns",
            "inbound": "sync_inbound",
            "outbound": "sync_outbound",
            "stock_allocations": "sync_stock_allocations",
            "shop_orders": "sync_shop_orders",
        }
        fn = method_map.get(job_type)
        if not fn:
            _record("jackyun", job_type, "failed", f"未知任务类型 {job_type}")
            return {"status": "unknown"}
        job = start_sync_job(db, "jackyun", job_type)
        try:
            stats = getattr(adapter, fn)()
            finish_sync_job(db, job, "success", stats or {})
            db.add(SyncLog(
                provider="jackyun", level="info", sync_job_id=job.id,
                message=f"{job_type} 同步完成", data={"stats": stats or {}},
            ))
            db.commit()
            _set_jackyun_connection(db, "connected")
            return {"status": "success", "stats": stats}
        except NotImplementedError:
            finish_sync_job(db, job, "skipped", {}, "mapping 待 Phase 1 真实字段样本")
            return {"status": "pending_mapping"}
        except AdapterPermissionError as exc:
            message = str(exc)[:500]
            _finish_attempt(db, job.id, "failed", message)
            _set_jackyun_connection(db, "blocked", message)
            ensure_exception(db, "JACKYUN_SYNC_FAIL", "吉客云同步失败", str(exc))
            return {"status": "blocked", "error": message}
        except Exception as exc:  # 指数退避重试
            message = str(exc)[:500]
            _finish_attempt(db, job.id, "failed", message)
            _set_jackyun_connection(db, "error", message)
            ensure_exception(db, "JACKYUN_SYNC_FAIL", "吉客云同步失败", message)
            raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
    finally:
        db.close()


@celery_app.task(name="tasks.sync_1688", bind=True, max_retries=2, default_retry_delay=180)
def sync_1688(
    self,
    mode: str = "incremental",
    start_date: str | None = None,
    end_date: str | None = None,
    order_no: str | None = None,
    supplier: str | None = None,
    keyword: str | None = None,
    only_unfinished: bool = False,
    time_field: str = "order_time",
) -> dict[str, Any]:
    """1688 订单同步：浏览器直采为主通道，开放平台 OAuth 保留为备用。"""
    db = SessionLocal()
    try:
        from app.config import settings as s

        if s.ALIBABA_1688_BROWSER_ENABLED:
            from app.services.alibaba1688_browser_sync_service import BrowserCaptureError, sync_orders

            try:
                return sync_orders(
                    db,
                    actor="system",
                    mode=mode,
                    start_date=start_date,
                    end_date=end_date,
                    order_no=order_no,
                    supplier=supplier,
                    keyword=keyword,
                    only_unfinished=only_unfinished,
                    time_field=time_field,
                )
            except BrowserCaptureError as exc:
                raise self.retry(exc=exc, countdown=180 * (2 ** self.request.retries))
        if not s.alibaba_1688_configured:
            _record("alibaba_1688", "orders", "skipped", "等待 1688 配置（浏览器通道已关闭）")
            return {"status": "skipped"}
        _record("alibaba_1688", "orders", "failed", "OAuth 交换与订单拉取待真实凭证后实现")
        return {"status": "pending_credentials"}
    finally:
        db.close()


@celery_app.task(name="tasks.login_1688")
def login_1688() -> dict[str, Any]:
    db = SessionLocal()
    try:
        from app.services.alibaba1688_browser_sync_service import run_login
        return run_login(db, actor="system")
    finally:
        db.close()


@celery_app.task(name="tasks.sync_jky_web", soft_time_limit=4500, time_limit=5400)
def sync_jky_web() -> dict[str, Any]:
    db = SessionLocal()
    try:
        from app.services.jky_web_sync_service import sync_all
        return sync_all(db, actor="system", include_sales=False)
    finally:
        db.close()


@celery_app.task(name="tasks.sync_jky_orders", soft_time_limit=1800, time_limit=2100)
def sync_jky_orders() -> dict[str, Any]:
    db = SessionLocal()
    try:
        from app.services.jky_order_sync_service import sync_orders
        return sync_orders(db, actor="system")
    finally:
        db.close()


@celery_app.task(name="tasks.monthly_verify", bind=True, max_retries=3, default_retry_delay=60)
def monthly_verify(self) -> dict[str, Any]:
    """月初对上月完整校验：销售汇总生成后再检查财务资料缺项。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.services import finance_service
    from app.services.integration_service import ensure_exception

    today = datetime.now(ZoneInfo(settings.TZ)).date()
    y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    db = SessionLocal()
    try:
        period = finance_service.refresh_period_status(db, finance_service.DEFAULT_COMPANY, y, m)
        missing = (period.missing_summary or {}).get("missing", {})
        if period.status != "SENT" and missing:
            ensure_exception(
                db, "FINANCE_INCOMPLETE", f"财务资料缺失 {y}-{m:02d}",
                f"缺少: {missing}（月度完整性检查）",
            )
            _record("system", "monthly_verify", "failed", f"{y}-{m:02d} 财务资料不完整: {missing}")
            return {"status": "incomplete", "period": f"{y}-{m:02d}", "missing": missing}
        _record("system", "monthly_verify", "success", f"{y}-{m:02d} 资料完整")
        return {"status": "complete", "period": f"{y}-{m:02d}"}
    except Exception as exc:
        _record("system", "monthly_verify", "failed", str(exc)[:500])
        ensure_exception(db, "MONTHLY_VERIFY_FAIL", "月度校验失败", str(exc))
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
    finally:
        db.close()


@celery_app.task(name="tasks.generate_monthly_sales_outbound", bind=True, max_retries=2, default_retry_delay=120)
def generate_monthly_sales_outbound(self, year: int | None = None, month: int | None = None) -> dict[str, Any]:
    """保留吉客云销售出库原始口径 CSV，作为财务底稿之一。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.services import finance_service, sales_outbound_report_service as svc

    if year is None or month is None:
        today = datetime.now(ZoneInfo(settings.TZ)).date()
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        year, month = y, m
    db = SessionLocal()
    try:
        rep = svc.build_report(db, year, month)
        csv_bytes = svc.to_csv(rep)
        row = finance_service.store_upload(
            db,
            company=finance_service.DEFAULT_COMPANY,
            year=year, month=month,
            category="jackyun",
            original_name=f"销售出库_{year}{month:02d}.csv",
            content=csv_bytes,
            actor="system",
        )
        _record("system", "sales_outbound_monthly", "success",
                f"{year}-{month:02d} 销售出库报表已归档（{rep['summary']['docCount']} 单）")
        return {"status": "ok", "period": f"{year}-{month:02d}",
                "archiveFileId": row.id, "docCount": rep["summary"]["docCount"]}
    except Exception as exc:
        _record("system", "sales_outbound_monthly", "failed", str(exc)[:500])
        raise self.retry(exc=exc, countdown=120 * (2 ** self.request.retries))
    finally:
        db.close()


@celery_app.task(name="tasks.generate_monthly_finance_sales_report", bind=True, max_retries=2, default_retry_delay=120)
def generate_monthly_finance_sales_report(
    self, year: int | None = None, month: int | None = None
) -> dict[str, Any]:
    """按用户保存的字段模板生成上月销售汇总 XLSX，并版本化归档。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.services import finance_sales_report_service as report_service
    from app.services import finance_service

    if year is None or month is None:
        today = datetime.now(ZoneInfo(settings.TZ)).date()
        year, month = (
            (today.year, today.month - 1)
            if today.month > 1
            else (today.year - 1, 12)
        )
    db = SessionLocal()
    try:
        result = report_service.generate_and_archive(
            db,
            company=finance_service.DEFAULT_COMPANY,
            year=year,
            month=month,
            actor="system",
            skip_if_exists=True,
        )
        _record(
            "system", "finance_sales_report_monthly", "success",
            f"{year}-{month:02d} 销售汇总：{result['status']}", result,
        )
        return result
    except Exception as exc:
        _record("system", "finance_sales_report_monthly", "failed", str(exc)[:500])
        raise self.retry(exc=exc, countdown=120 * (2 ** self.request.retries))
    finally:
        db.close()


@celery_app.task(name="tasks.finance_auto_delivery")
def finance_auto_delivery() -> dict[str, Any]:
    """按模板自动发送财务包。

    每小时检查一次，但只在模板设定小时执行；从 send_day 起每天重试，直到资料齐全并发送成功。
    已发送账期直接跳过，避免重复发送；真正发送前始终基于当前资料生成新版本，
    并锁定发送该版本，避免复用历史 PACKAGED 包。
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.models.finance import FinanceSalesReportTemplate
    from app.services import finance_sales_report_service as report_service
    from app.services import finance_service

    now = datetime.now(ZoneInfo(settings.TZ))
    db = SessionLocal()
    results: list[dict[str, Any]] = []
    try:
        templates = (
            db.query(FinanceSalesReportTemplate)
            .filter_by(enabled=True, auto_send=True)
            .all()
        )
        for template in templates:
            if now.day < template.send_day or now.hour != template.send_hour:
                continue
            if now.month > 1:
                year, month = now.year, now.month - 1
            else:
                year, month = now.year - 1, 12

            report_service.generate_and_archive(
                db,
                company=template.company,
                year=year,
                month=month,
                actor="system",
                skip_if_exists=True,
            )
            period = finance_service.refresh_period_status(db, template.company, year, month)
            if period.status == "SENT":
                results.append({"company": template.company, "status": "already_sent"})
                continue
            missing = (period.missing_summary or {}).get("missing", {})
            if missing:
                results.append({"company": template.company, "status": "waiting_files", "missing": missing})
                continue
            if not template.to_addrs:
                results.append({"company": template.company, "status": "missing_recipient"})
                continue

            try:
                # 发送前重新生成当前版本，确保调整后的无票收入/对公付款明细/银行资料
                # 不会被旧的 PACKAGED 版本覆盖；随后明确发送刚生成的 version。
                pkg = finance_service.package_period(
                    db, template.company, year, month, actor="system"
                )
                sent = finance_service.send_delivery(
                    db,
                    template.company,
                    year,
                    month,
                    version=pkg.version,
                    to_addrs=list(template.to_addrs or []),
                    cc_addrs=list(template.cc_addrs or []),
                    actor="system",
                )
                results.append({"company": template.company, "status": "sent", **sent})
            except AdapterNotConfigured as exc:
                results.append({"company": template.company, "status": "smtp_unconfigured", "detail": str(exc)})
            except (ValueError, RuntimeError) as exc:
                results.append({"company": template.company, "status": "send_failed", "detail": str(exc)[:500]})
        return {"status": "ok", "checkedAt": now.isoformat(), "results": results}
    finally:
        db.close()


@celery_app.task(name="tasks.generate_monthly_accounting_summary", bind=True, max_retries=2, default_retry_delay=120)
def generate_monthly_accounting_summary(self, year: int | None = None, month: int | None = None) -> dict[str, Any]:
    """生成实际给财务发送的销售开票大类汇总，不附逐条商品/SKU明细。"""
    from datetime import date

    from app.services import finance_service
    from app.services import tax_finance_summary_service as summary_service
    from app.services.integration_service import ensure_exception

    if year is None or month is None:
        today = date.today()
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        year, month = y, m
    db = SessionLocal()
    try:
        report = summary_service.build_finance_summary(db, year, month)
        if not report["readyForFinanceDelivery"]:
            detail = f"阻塞项 {report['summary']['blockerCount']} 条"
            ensure_exception(
                db,
                "FINANCE_ACCOUNTING_SUMMARY_BLOCKED",
                f"财务大类汇总不可发送 {year}-{month:02d}",
                detail,
            )
            _record("system", "accounting_summary_monthly", "failed", f"{year}-{month:02d} {detail}")
            return {
                "status": "blocked",
                "period": f"{year}-{month:02d}",
                "blockers": report["blockers"],
            }
        content = summary_service.to_finance_csv(report)
        row = finance_service.store_upload(
            db,
            company=finance_service.DEFAULT_COMPANY,
            year=year,
            month=month,
            category="invoice",
            original_name=f"销售开票分类汇总_{year}{month:02d}.csv",
            content=content,
            actor="system",
        )
        _record(
            "system",
            "accounting_summary_monthly",
            "success",
            f"{year}-{month:02d} 财务大类汇总已归档（{report['summary']['categoryRowCount']} 类）",
        )
        return {
            "status": "ok",
            "period": f"{year}-{month:02d}",
            "archiveFileId": row.id,
            "categoryRowCount": report["summary"]["categoryRowCount"],
            "detailRetained": True,
        }
    except Exception as exc:
        _record("system", "accounting_summary_monthly", "failed", str(exc)[:500])
        raise self.retry(exc=exc, countdown=120 * (2 ** self.request.retries))
    finally:
        db.close()
