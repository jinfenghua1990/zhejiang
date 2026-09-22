"""1688 浏览器直采编排服务：Redis 互斥锁 → 登录检查 → 逐页捕获 → 增量 upsert → 虚拟批次。

架构定位（1688 Browser Sync V1）：
- 浏览器直采为主通道：Playwright 驱动真实 Chrome 打开「已买到的货品」，
  截获页面自身的 mtop 响应，解析订单后走与 Excel 导入完全相同的落库链路
  （upsert_order_data → Alibaba1688Order + ExternalPurchaseOrder）。
- 同步与登录共用一个持久化 Profile，同一时刻只允许一个浏览器实例
  （Redis SETNX 锁保证，跨 worker/beat/手动触发互斥）。
- 增量策略：订单列表按时间倒序，遇到连续 N 条已知订单即停止翻页，
  每天通常只访问 1~3 页，比全量翻页稳定得多。

本服务不做浏览器细节（见 adapters/alibaba1688_browser.py）与字段映射
（见 services/alibaba1688_mtop_mapper.py），只负责编排、落库与状态记录。
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.adapters.alibaba1688_browser import (
    Alibaba1688BrowserAdapter,
    BrowserCaptureError,
    BrowserLoginExpiredError,
    BrowserRiskControlError,
)
from app.adapters.jackyun import finish_sync_job, start_sync_job
from app.config import settings
from app.core.audit import audit
from app.models.alibaba1688_import import Alibaba1688FileImport, Alibaba1688Order
from app.models.integration import IntegrationConnection, SyncLog
from app.services.alibaba1688_import_service import upsert_order_data
from app.services.alibaba1688_mtop_mapper import extract_orders, map_order
from app.services.integration_service import ensure_exception, get_or_create_connection

PROVIDER = "alibaba_1688"
CONNECTION_MODE = "浏览器直采"
# 同一持久化 Profile 同时只允许一个浏览器实例：同步任务与扫码登录都抢这把锁。
PROFILE_LOCK_KEY = "ecommerce:lock:alibaba1688-browser-profile"
PROFILE_LOCK_TTL_SECONDS = 15 * 60
VIRTUAL_BATCH_PATH_MARKER = "browser://direct-capture"
_TIME_FIELD_LABELS = {
    "order_time": "下单时间",
    "pay_time": "付款时间",
}
_CLOSED_ORDER_STATUS_RE = re.compile(
    r"交易关闭|订单关闭|已关闭|关闭|已取消|交易取消|取消|closed|cancelled|canceled|cancel",
    re.IGNORECASE,
)


def _is_closed_order_status(value: Any) -> bool:
    """判断 1688 订单是否属于关闭/取消状态，不让其进入采购订单表。"""
    return bool(_CLOSED_ORDER_STATUS_RE.search(str(value or "").strip()))


def _normalize_time_field(value: str | None) -> str:
    field = (value or "order_time").strip()
    if field not in _TIME_FIELD_LABELS:
        raise ValueError("不支持的 1688 时间维度")
    return field


def _matches_supplier(data: dict[str, Any], supplier: str) -> bool:
    """按供应商公司名或供应商账号匹配，允许输入公司名的一部分。"""
    needle = supplier.strip().casefold()
    if not needle:
        return True
    return any(
        needle in str(data.get(field) or "").strip().casefold()
        for field in ("seller_company_name", "seller_member_name")
    )


def _matches_keyword(data: dict[str, Any], keyword: str) -> bool:
    """在映射字段和原始 1688 响应中查找商品名、SKU 等关键词。"""
    needle = keyword.strip().casefold()
    if not needle:
        return True
    mapped_text = " ".join(str(value or "") for key, value in data.items() if not key.startswith("_"))
    raw_text = json.dumps(data.get("_raw") or {}, ensure_ascii=False, default=str)
    return needle in f"{mapped_text} {raw_text}".casefold()


_FINISHED_ORDER_STATUS_RE = re.compile(
    r"(?:交易成功|已完成|已收货|已签收|success|finished|completed)",
    re.IGNORECASE,
)


def _is_finished_order_status(value: Any) -> bool:
    """识别明确已完成的订单；不把待完成等中间状态误判为完成。"""
    return bool(_FINISHED_ORDER_STATUS_RE.search(str(value or "").strip()))


def _parse_sync_date(value: str | date | None, field_name: str) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是 YYYY-MM-DD") from exc


def _date_boundary(value: date) -> datetime:
    try:
        local_tz = ZoneInfo(settings.TZ)
    except Exception:  # noqa: BLE001 - 时区配置异常时回退 UTC
        local_tz = timezone.utc
    return datetime.combine(value, time.min, tzinfo=local_tz).astimezone(timezone.utc)


def _normalize_sync_scope(
    mode: str,
    start_date: str | date | None,
    end_date: str | date | None,
    order_no: str | None,
) -> tuple[date | None, date | None, str | None]:
    if mode not in {"incremental", "range", "single"}:
        raise ValueError("不支持的 1688 拉取模式")
    if mode == "single":
        target = (order_no or "").strip()
        if not target:
            raise ValueError("单个补拉必须填写 1688 采购订单号")
        return None, None, target
    if mode == "range":
        start = _parse_sync_date(start_date, "开始日期")
        end = _parse_sync_date(end_date, "结束日期")
        if start is None or end is None:
            raise ValueError("按时间范围拉取必须填写开始日期和结束日期")
        if start > end:
            raise ValueError("开始日期不能晚于结束日期")
        return start, end, None
    return None, None, None


class SyncAlreadyRunningError(Exception):
    """同一 Profile 的浏览器任务已在执行（同步或登录）。"""


class _ProfileLock:
    """Redis SETNX 互斥锁；释放时校验 token 防止误删他人持有的锁。"""

    def __init__(self, key: str = PROFILE_LOCK_KEY, ttl_s: int = PROFILE_LOCK_TTL_SECONDS) -> None:
        self.key = key
        self.ttl_s = ttl_s
        self.token = secrets.token_hex(8)

    def __enter__(self) -> "_ProfileLock":
        import redis as redis_lib

        client = redis_lib.from_url(settings.REDIS_URL)
        ok = client.set(self.key, self.token, nx=True, ex=self.ttl_s)
        client.close()
        if not ok:
            raise SyncAlreadyRunningError("1688 浏览器任务已在执行中（同步或扫码登录占用 Profile）")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        import redis as redis_lib

        client = redis_lib.from_url(settings.REDIS_URL)
        try:
            # 仍持有同一 token 才删除，避免超时后误删他人新锁。
            if client.get(self.key) == self.token.encode():
                client.delete(self.key)
        finally:
            client.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connection(db: Session) -> IntegrationConnection:
    return get_or_create_connection(db, PROVIDER, CONNECTION_MODE, 4)


def _set_connection(
    db: Session,
    status: str,
    message: str = "",
    **meta_updates: Any,
) -> IntegrationConnection:
    conn = _connection(db)
    conn.status = status
    conn.error_summary = (message or "")[:500]
    meta = dict(conn.meta or {})
    meta.update(meta_updates)
    conn.meta = meta
    if status == "connected":
        conn.last_success_at = datetime.now(timezone.utc)
    conn.last_tested_at = datetime.now(timezone.utc)
    db.commit()
    return conn


def browser_sync_status(db: Session) -> dict[str, Any]:
    """只读本地库的浏览器通道状态（不打开浏览器，供前端轮询）。"""
    conn = (
        db.query(IntegrationConnection)
        .filter_by(provider=PROVIDER, mode=CONNECTION_MODE)
        .first()
    )
    last_sync_log = (
        db.query(SyncLog)
        .filter(SyncLog.provider == PROVIDER)
        .order_by(SyncLog.id.desc())
        .first()
    )
    meta = dict(conn.meta or {}) if conn else {}
    return {
        "enabled": settings.ALIBABA_1688_BROWSER_ENABLED,
        "profileDir": settings.alibaba_1688_browser_profile_dir,
        "headless": settings.ALIBABA_1688_BROWSER_HEADLESS,
        "status": conn.status if conn else "unconfigured",
        "account": meta.get("account"),
        "lastSyncAt": meta.get("lastSyncAt"),
        "lastSyncSummary": meta.get("lastSyncSummary"),
        "lastLoginCheckAt": meta.get("lastLoginCheckAt"),
        "errorSummary": (conn.error_summary or None) if conn else None,
        "lastSyncJobId": last_sync_log.sync_job_id if last_sync_log else None,
        "maxPages": settings.ALIBABA_1688_BROWSER_MAX_PAGES,
        "stopAfterKnown": settings.ALIBABA_1688_BROWSER_STOP_AFTER_KNOWN,
        "lookbackDays": settings.ALIBABA_1688_BROWSER_LOOKBACK_DAYS,
    }


def _dump_capture_samples(adapter: Alibaba1688BrowserAdapter) -> Path:
    """首捕调试：把原始 mtop 响应落盘，供回填字段映射路径。"""
    root = Path(settings.DATA_DIR).resolve() / "alibaba1688-mtop-samples"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = root / f"{stamp}-{secrets.token_hex(4)}.json"
    target.write_text(
        json.dumps(adapter.raw_responses, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    target.chmod(0o600)
    return target


def _create_virtual_batch(db: Session, actor: str, row_count: int) -> Alibaba1688FileImport:
    """浏览器同步批次：无原件文件，仅作为订单归属与生命周期载体。"""
    now = datetime.now(timezone.utc)
    token = secrets.token_hex(16)
    return Alibaba1688FileImport(
        original_name=f"浏览器直采 {now.strftime('%Y-%m-%d %H:%M')}",
        stored_path=VIRTUAL_BATCH_PATH_MARKER,
        sha256=hashlib.sha256(f"browser-sync:{token}".encode()).hexdigest(),
        size=0,
        mime="application/json",
        status="completed",
        sheet_name="mtop.1688.trading.dataline.service",
        headers=[],
        row_count=row_count,
        imported_order_count=0,
        error_summary="",
        uploader=actor,
        lifecycle="active",
        lifecycle_changed_at=now,
        parsed_at=now,
    )


def sync_orders(
    db: Session,
    *,
    actor: str = "system",
    mode: str = "incremental",
    start_date: str | date | None = None,
    end_date: str | date | None = None,
    order_no: str | None = None,
    supplier: str | None = None,
    keyword: str | None = None,
    only_unfinished: bool = False,
    time_field: str = "order_time",
) -> dict[str, Any]:
    """主同步入口：按增量、时间范围或单号逐页捕获并幂等落库。"""
    if not settings.ALIBABA_1688_BROWSER_ENABLED:
        return {"status": "skipped", "reason": "browser channel disabled"}

    scope_start, scope_end, target_order_no = _normalize_sync_scope(
        mode, start_date, end_date, order_no
    )
    normalized_time_field = _normalize_time_field(time_field)
    supplier_filter = (supplier or "").strip()
    keyword_filter = (keyword or "").strip()

    job = start_sync_job(db, PROVIDER, "browser_orders")
    try:
        with _ProfileLock():
            result = _sync_with_browser(
                db,
                actor=actor,
                mode=mode,
                scope_start=scope_start,
                scope_end=scope_end,
                target_order_no=target_order_no,
                supplier_filter=supplier_filter,
                keyword_filter=keyword_filter,
                only_unfinished=only_unfinished,
                time_field=normalized_time_field,
            )
        job_status = "success" if result.get("status") == "success" else result.get("status", "failed")
        finish_sync_job(db, job, job_status, result.get("stats") or {}, result.get("message", ""))
        if result.get("status") == "success":
            # 同步恢复：自动关闭历史 1688 采集/配置故障告警（ensure_exception 的对称操作）
            from app.services.integration_service import resolve_exception

            resolve_exception(db, "ALIBABA_1688_BROWSER_CAPTURE_FAIL",
                              f"浏览器直采恢复（job #{job.id}），自动关闭")
            resolve_exception(db, "ALIBABA1688_NOT_CONFIGURED", "浏览器直采成功，配置有效，自动关闭")
        db.add(SyncLog(
            provider=PROVIDER,
            level="info" if result.get("status") == "success" else "warn",
            sync_job_id=job.id,
            message=result.get("message", ""),
            data={"stats": result.get("stats") or {}},
        ))
        db.commit()
        return result
    except SyncAlreadyRunningError as exc:
        message = str(exc)
        finish_sync_job(db, job, "skipped", {}, message)
        db.add(SyncLog(provider=PROVIDER, level="warn", sync_job_id=job.id, message=message))
        db.commit()
        return {"status": "already_running", "message": message}
    except BrowserLoginExpiredError as exc:
        message = str(exc)
        _set_connection(db, "needs_login", message, lastLoginCheckAt=_now_iso())
        finish_sync_job(db, job, "needs_login", {}, message)
        db.add(SyncLog(provider=PROVIDER, level="warn", sync_job_id=job.id, message=message))
        db.commit()
        return {"status": "login_required", "message": message}
    except BrowserRiskControlError as exc:
        message = str(exc)
        _set_connection(db, "error", message)
        finish_sync_job(db, job, "failed", {}, message)
        db.add(SyncLog(provider=PROVIDER, level="error", sync_job_id=job.id, message=message))
        ensure_exception(db, "ALIBABA_1688_BROWSER_RISK", "1688 浏览器直采触发风控", message)
        db.commit()
        return {"status": "risk_control", "message": message}
    except BrowserCaptureError as exc:
        message = str(exc)
        _set_connection(db, "error", message)
        finish_sync_job(db, job, "failed", {}, message)
        db.add(SyncLog(provider=PROVIDER, level="error", sync_job_id=job.id, message=message))
        ensure_exception(db, "ALIBABA_1688_BROWSER_CAPTURE_FAIL", "1688 浏览器直采捕获失败", message)
        db.commit()
        return {"status": "capture_failed", "message": message}
    except Exception as exc:  # noqa: BLE001 - 记录后由 Celery 重试
        message = str(exc)[:500]
        try:
            _set_connection(db, "error", message)
            finish_sync_job(db, job, "failed", {}, message)
            db.add(SyncLog(provider=PROVIDER, level="error", sync_job_id=job.id, message=message))
            db.commit()
        except Exception:  # noqa: BLE001 - 状态记录失败不掩盖原始异常
            db.rollback()
        raise


def _sync_with_browser(
    db: Session,
    *,
    actor: str,
    mode: str,
    scope_start: date | None,
    scope_end: date | None,
    target_order_no: str | None,
    supplier_filter: str,
    keyword_filter: str,
    only_unfinished: bool,
    time_field: str,
) -> dict[str, Any]:
    """持锁执行浏览器捕获与落库（锁由调用方管理）。"""
    with Alibaba1688BrowserAdapter() as adapter:
        logged_in, account = adapter.check_login()
        _set_connection(
            db,
            "connected" if logged_in else "needs_login",
            "",
            lastLoginCheckAt=_now_iso(),
            **({"account": account} if account else {}),
        )
        if logged_in:
            # 会话有效即刷新登录态备份，保持 state JSON 与最新会话同步。
            adapter.export_state()
        if not logged_in:
            return {
                "status": "login_required",
                "message": "1688 登录态失效，请发起扫码登录",
            }

        if settings.ALIBABA_1688_BROWSER_CAPTURE_ONLY:
            # 首捕调试：只采样落盘，不写订单表。
            for _ in adapter.iter_order_pages():
                pass
            sample_path = _dump_capture_samples(adapter)
            return {
                "status": "capture_only",
                "message": f"首捕调试完成，原始响应已保存 {sample_path}",
                "stats": {"sampleFile": str(sample_path), "responses": len(adapter.raw_responses)},
            }

        known_ids = {
            row[0]
            for row in db.query(Alibaba1688Order.external_order_id)
            .filter(Alibaba1688Order.row_status != "deleted")
            .all()
        }
        deleted_ids = {
            row[0]
            for row in db.query(Alibaba1688Order.external_order_id)
            .filter(Alibaba1688Order.row_status == "deleted")
            .all()
        }
        counters: dict[str, int] = {
            "created": 0, "adopted": 0, "merged": 0,
            "restored": 0, "duplicates": 0, "skippedDeleted": 0, "skippedNoId": 0,
            "skippedOutOfWindow": 0, "skippedNonTarget": 0, "skippedClosed": 0,
            "skippedSupplier": 0, "skippedKeyword": 0, "skippedFinished": 0,
        }
        new_order_numbers: set[str] = set()
        consecutive_known = 0
        pages_visited = 0
        raw_seen = 0
        target_found = False
        target_closed = False
        target_closed_status = ""
        target_supplier_mismatch = False
        stop_reason = "end"
        lookback_days = max(int(settings.ALIBABA_1688_BROWSER_LOOKBACK_DAYS), 1)
        lookback_cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        range_start = _date_boundary(scope_start) if scope_start else None
        range_end_exclusive = _date_boundary(scope_end + timedelta(days=1)) if scope_end else None

        batch = _create_virtual_batch(db, actor, row_count=0)
        db.add(batch)
        db.flush()

        for page_responses in adapter.iter_order_pages():
            pages_visited += 1
            page_order_count = 0
            page_has_new_order = False
            page_time_values: list[datetime] = []
            seen_on_page: set[str] = set()
            for response in page_responses:
                for raw_order in extract_orders(response):
                    data = map_order(raw_order)
                    order_id = str(data.get("external_order_id") or "").strip()
                    # 同一页的多条 mtop 响应可能包含重复订单，按订单号去重。
                    if order_id and order_id in seen_on_page:
                        counters["duplicates"] += 1
                        continue
                    if order_id:
                        seen_on_page.add(order_id)
                    page_order_count += 1
                    if not order_id:
                        counters["skippedNoId"] += 1
                        continue
                    selected_time = data.get(time_field)
                    if isinstance(selected_time, datetime):
                        if selected_time.tzinfo is None:
                            selected_time = selected_time.replace(tzinfo=timezone.utc)
                        page_time_values.append(selected_time)
                    if mode == "single" and order_id != target_order_no:
                        counters["skippedNonTarget"] += 1
                        continue
                    if mode == "range" and (
                        not isinstance(selected_time, datetime)
                        or range_start is None
                        or range_end_exclusive is None
                        or selected_time < range_start
                        or selected_time >= range_end_exclusive
                    ):
                        counters["skippedOutOfWindow"] += 1
                        continue
                    if supplier_filter and not _matches_supplier(data, supplier_filter):
                        counters["skippedSupplier"] += 1
                        if mode == "single":
                            target_supplier_mismatch = True
                        continue
                    if keyword_filter and not _matches_keyword(data, keyword_filter):
                        counters["skippedKeyword"] += 1
                        continue
                    if mode == "single":
                        if _is_closed_order_status(data.get("order_status")):
                            counters["skippedClosed"] += 1
                            target_closed = True
                            target_closed_status = str(data.get("order_status") or "关闭")
                            continue
                        target_found = True
                    if _is_closed_order_status(data.get("order_status")):
                        counters["skippedClosed"] += 1
                        if mode == "incremental":
                            # 关闭订单也属于已识别记录，避免每次增量都反复翻过同一页。
                            consecutive_known += 1
                        continue
                    if only_unfinished and _is_finished_order_status(data.get("order_status")):
                        counters["skippedFinished"] += 1
                        continue
                    if mode != "single" and order_id in deleted_ids:
                        counters["skippedDeleted"] += 1
                        if mode == "incremental":
                            consecutive_known += 1
                        continue
                    raw_seen += 1
                    if order_id in known_ids:
                        counters["duplicates"] += 1
                        if mode == "incremental":
                            consecutive_known += 1
                    elif order_id in deleted_ids:
                        consecutive_known = 0
                        new_order_numbers.add(order_id)
                    else:
                        page_has_new_order = True
                        consecutive_known = 0
                        known_ids.add(order_id)
                        new_order_numbers.add(order_id)
                    outcome = upsert_order_data(
                        db, data,
                        import_id=batch.id,
                        source="alibaba1688_browser",
                        adopt_from_deleted=True,
                        update_status=True,
                        restore_deleted=mode == "single",
                    )
                    if outcome == "skipped_deleted":
                        counters["skippedDeleted"] += 1
                    elif outcome == "skipped_no_id":
                        counters["skippedNoId"] += 1
                    else:
                        counters[outcome] += 1
            if page_order_count == 0:
                # 页面响应里没有订单：可能是末页，也可能是登录态/页面结构异常。
                stop_reason = "empty_page"
                break
            if target_closed:
                stop_reason = "target_closed"
                break
            if mode == "single" and target_found:
                stop_reason = "target_found"
                break
            if mode == "range":
                page_is_older_than_range = bool(page_time_values) and range_start is not None and all(
                    selected_time < range_start for selected_time in page_time_values
                )
                if page_is_older_than_range:
                    stop_reason = "date_range"
                    break
            if mode == "incremental":
                page_is_older_than_window = bool(page_time_values) and all(
                    selected_time < lookback_cutoff for selected_time in page_time_values
                )
                if page_is_older_than_window and not page_has_new_order:
                    # 已知阈值仍作为兼容性兜底，但不再是唯一的停止条件。
                    stop_reason = (
                        "known_threshold"
                        if consecutive_known >= settings.ALIBABA_1688_BROWSER_STOP_AFTER_KNOWN
                        else "lookback_window"
                    )
                    break
                if consecutive_known >= settings.ALIBABA_1688_BROWSER_STOP_AFTER_KNOWN:
                    # 订单通常按时间倒序：连续 N 条都是已知订单，作为保守兜底停止。
                    stop_reason = "known_threshold"
                    break
        else:
            stop_reason = "last_page" if pages_visited else "empty_page"

        capture_path = _dump_capture_samples(adapter)
        capture_complete = stop_reason != "empty_page"

        if mode == "single" and target_closed:
            db.delete(batch)
            db.commit()
            message = (
                f"1688 订单号 {target_order_no} 当前状态为“{target_closed_status or '关闭'}”，"
                "已跳过，未写入采购订单"
            )
            _set_connection(
                db,
                "connected",
                "",
                account=account,
                lastSyncAt=_now_iso(),
                lastSyncSummary=message,
                lastCaptureFile=str(capture_path),
            )
            db.commit()
            return {
                "status": "closed_skipped",
                "message": message,
                "stats": {
                    "mode": mode,
                    "pagesVisited": pages_visited,
                    "stopReason": stop_reason,
                    "captureComplete": capture_complete,
                    "rawCaptureFile": str(capture_path),
                    "batchId": None,
                    "startDate": scope_start.isoformat() if scope_start else None,
                    "endDate": scope_end.isoformat() if scope_end else None,
                    "orderNo": target_order_no,
                    "supplier": supplier_filter or None,
                    "keyword": keyword_filter or None,
                    "onlyUnfinished": only_unfinished,
                    "timeField": time_field,
                    "closedStatus": target_closed_status or "关闭",
                    **counters,
                },
            }

        if (mode == "single" and not target_found) or (mode == "range" and raw_seen == 0):
            db.delete(batch)
            db.commit()
            message = (
                (
                    f"1688 订单号 {target_order_no} 不属于供应商“{supplier_filter}”，未写入订单"
                    if target_supplier_mismatch
                    else f"最近 {pages_visited} 页未找到 1688 订单号 {target_order_no}，未写入订单"
                )
                if mode == "single"
                else (
                    "所选时间范围内未找到可导入的 1688 采购订单，"
                    f"已跳过供应商不匹配 {counters['skippedSupplier']} 单、关闭订单 {counters['skippedClosed']} 单，未写入订单"
                    if counters["skippedSupplier"] or counters["skippedClosed"] or counters["skippedKeyword"] or counters["skippedFinished"]
                    else "所选时间范围内未找到 1688 采购订单，未写入订单"
                )
            )
            _set_connection(
                db,
                "connected",
                "",
                account=account,
                lastSyncAt=_now_iso(),
                lastSyncSummary=message,
                lastCaptureFile=str(capture_path),
            )
            db.commit()
            return {
                "status": "not_found",
                "message": message,
                "stats": {
                    "mode": mode,
                    "pagesVisited": pages_visited,
                    "stopReason": stop_reason,
                    "captureComplete": capture_complete,
                    "rawCaptureFile": str(capture_path),
                    "batchId": None,
                    "startDate": scope_start.isoformat() if scope_start else None,
                    "endDate": scope_end.isoformat() if scope_end else None,
                    "orderNo": target_order_no,
                    "supplier": supplier_filter or None,
                    "keyword": keyword_filter or None,
                    "onlyUnfinished": only_unfinished,
                    "timeField": time_field,
                    **counters,
                },
            }

        if not capture_complete and raw_seen == 0:
            # 没有任何订单时删除空虚拟批次，明确记录为部分失败，避免页面显示“同步成功”。
            db.delete(batch)
            db.commit()
            message = "1688 页面未捕获到订单数据；未写入订单，请检查登录态或页面结构"
            _set_connection(
                db,
                "error",
                message,
                account=account,
                lastSyncAt=_now_iso(),
                lastSyncSummary=message,
                lastCaptureFile=str(capture_path),
            )
            ensure_exception(db, "ALIBABA_1688_BROWSER_EMPTY", "1688 未捕获订单数据", message)
            db.commit()
            return {
                "status": "partial",
                "message": message,
                "stats": {
                    "pagesVisited": pages_visited,
                    "stopReason": stop_reason,
                    "supplier": supplier_filter or None,
                    "keyword": keyword_filter or None,
                    "onlyUnfinished": only_unfinished,
                    "timeField": time_field,
                    "captureComplete": False,
                    "rawCaptureFile": str(capture_path),
                    "batchId": None,
                    **counters,
                },
            }

        batch.row_count = raw_seen
        batch.imported_order_count = counters["created"] + counters["adopted"] + counters["restored"]
        db.commit()

        # 与 Excel 导入对齐：新订单可能先于入库明细存在，补齐 SKU 分配行。
        from app.services.inbound_allocation_seed import seed_allocations_for_order_numbers

        seed_allocations_for_order_numbers(db, new_order_numbers)

        from app.services.alibaba1688_remark_match_service import run_verified_remark_match

        remark_match = run_verified_remark_match(db, actor=actor)

        scope_summary = (
            f"{_TIME_FIELD_LABELS[time_field]} {scope_start.isoformat()} 至 {scope_end.isoformat()}"
            if mode == "range" and scope_start and scope_end
            else f"订单号 {target_order_no}"
            if mode == "single" and target_order_no
            else f"默认增量（{_TIME_FIELD_LABELS[time_field]}）"
        )
        if supplier_filter:
            scope_summary += f"；供应商 {supplier_filter}"
        if keyword_filter:
            scope_summary += f"；关键词 {keyword_filter}"
        summary = (
            f"浏览器直采{'完成' if capture_complete else '部分完成'}（{scope_summary}）：新增 {counters['created']}，"
            f"已识别重复 {counters['duplicates']}，状态更新 {counters['merged']}，"
            f"跳过供应商不匹配 {counters['skippedSupplier']}、关键词不匹配 {counters['skippedKeyword']}、"
            f"已完成 {counters['skippedFinished']}、关闭 {counters['skippedClosed']}，"
            f"翻页 {pages_visited}（{stop_reason}），批次 #{batch.id}"
        )
        _set_connection(
            db,
            "connected" if capture_complete else "error",
            "" if capture_complete else "本次捕获在空页处提前结束，覆盖可能不完整",
            account=account,
            lastSyncAt=_now_iso(),
            lastSyncSummary=summary,
            lastBatchId=batch.id,
            lastCaptureFile=str(capture_path),
            lastRemarkMatch=remark_match,
        )
        audit(
            db,
            actor,
            "alibaba1688.browser_sync.sync",
            "alibaba1688_file_imports",
            batch.id,
            {
                "mode": mode,
                "pages": pages_visited,
                "stopReason": stop_reason,
                "lookbackDays": lookback_days,
                "startDate": scope_start.isoformat() if scope_start else None,
                "endDate": scope_end.isoformat() if scope_end else None,
                "orderNo": target_order_no,
                "supplier": supplier_filter or None,
                "keyword": keyword_filter or None,
                "onlyUnfinished": only_unfinished,
                "timeField": time_field,
                "captureComplete": capture_complete,
                "remarkMatch": remark_match,
                **counters,
            },
        )
        db.commit()
        return {
            "status": "success" if capture_complete else "partial",
            "message": summary,
            "stats": {
                "mode": mode,
                "pagesVisited": pages_visited,
                "stopReason": stop_reason,
                "lookbackDays": lookback_days,
                "startDate": scope_start.isoformat() if scope_start else None,
                "endDate": scope_end.isoformat() if scope_end else None,
                "orderNo": target_order_no,
                "supplier": supplier_filter or None,
                "keyword": keyword_filter or None,
                "onlyUnfinished": only_unfinished,
                "timeField": time_field,
                "lookbackCutoff": lookback_cutoff.isoformat(),
                "captureComplete": capture_complete,
                "rawCaptureFile": str(capture_path),
                "batchId": batch.id,
                "remarkMatch": remark_match,
                **counters,
            },
        }


def run_login(db: Session, *, actor: str = "system", timeout_s: int | None = None) -> dict[str, Any]:
    """扫码登录入口：在服务器上打开浏览器窗口等待用户扫码，成功后保存登录态。

    必须由 Celery worker 执行（长阻塞，最长 ALIBABA_1688_LOGIN_TIMEOUT_SECONDS）。
    """
    job = start_sync_job(db, PROVIDER, "browser_login")
    try:
        with _ProfileLock():
            with Alibaba1688BrowserAdapter() as adapter:
                account = adapter.wait_for_login(timeout_s)
                # 登录成功立刻导出 storageState 备份（含 session cookie），
                # 供下次启动注入，兜底 Profile cookie 加密/Keychain 异常场景。
                state_path = adapter.export_state()
        _set_connection(
            db, "connected", "",
            account=account, lastLoginCheckAt=_now_iso(),
            **({"stateFile": state_path} if state_path else {}),
        )
        finish_sync_job(db, job, "success", {"account": account or "", "stateExported": bool(state_path)}, "扫码登录成功")
        db.add(SyncLog(provider=PROVIDER, level="info", sync_job_id=job.id, message=f"扫码登录成功（{account or '未知账号'}）"))
        db.commit()
        audit(db, actor, "alibaba1688.browser_sync.login", "integration", None, {"account": account})
        db.commit()
        return {"status": "success", "message": f"扫码登录成功（{account or '未知账号'}）", "account": account}
    except SyncAlreadyRunningError as exc:
        message = str(exc)
        finish_sync_job(db, job, "skipped", {}, message)
        db.add(SyncLog(provider=PROVIDER, level="warn", sync_job_id=job.id, message=message))
        db.commit()
        return {"status": "already_running", "message": message}
    except TimeoutError as exc:
        message = str(exc)
        _set_connection(db, "needs_login", message)
        finish_sync_job(db, job, "failed", {}, message)
        db.add(SyncLog(provider=PROVIDER, level="warn", sync_job_id=job.id, message=message))
        db.commit()
        return {"status": "login_timeout", "message": message}
    except BrowserRiskControlError as exc:
        message = str(exc)
        _set_connection(db, "error", message)
        finish_sync_job(db, job, "failed", {}, message)
        db.add(SyncLog(provider=PROVIDER, level="error", sync_job_id=job.id, message=message))
        db.commit()
        return {"status": "risk_control", "message": message}
