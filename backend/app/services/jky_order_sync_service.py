"""吉客云销售订单三通道统一同步。

获取层只返回原始行；本模块负责规范化、身份识别、去重、幂等写入、游标和
状态记录。这样 Web、RPA、API 不会各自创建一套订单。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.base import AdapterNotConfigured, AdapterPermissionError
from app.adapters.jackyun import finish_sync_job, load_checkpoint
from app.adapters.jky_order_providers import (
    JkyProviderHealth,
    build_order_providers,
)
from app.adapters.jky_web import JkyWebError
from app.config import settings
from app.models.integration import IntegrationConnection, SyncCheckpoint, SyncJob, SyncLog
from app.models.sales import SalesOrder, SalesOrderItem


JOB_PROVIDER = "jky_order"
JOB_TYPE = "orders"
CONNECTION_MODE = "订单同步"
CHANNEL_LABELS = {
    "jky_web": "Web 网页同步",
    "jky_rpa": "Windows 桌面 RPA 导出",
    "jky_api": "吉客云 OpenAPI/MCP",
}


class JkyOrderSyncInProgress(Exception):
    """已有同一订单同步任务运行。"""


class JkyOrderNormalizationError(ValueError):
    """原始订单行缺少可确认的业务身份。"""


class JkyOrderIdentityConflict(ValueError):
    """多个本地订单命中了同一外部身份，必须人工处理。"""


@dataclass
class NormalizedJkyOrder:
    order_no: str
    source_provider: str
    source_order_id: str
    identity_keys: list[str]
    platform: str = ""
    order_type: str = "trade"
    order_status: str = ""
    pay_status: str = ""
    buyer_note: str = ""
    order_amount: Decimal | None = None
    paid_amount: Decimal | None = None
    currency: str = "CNY"
    ordered_at: datetime | None = None
    paid_at: datetime | None = None
    items: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _decimal(value: Any) -> Decimal | None:
    text = _text(value).replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _datetime(value: Any) -> datetime | None:
    if value in (None, "", 0, "0"):
        return None
    tz = ZoneInfo(settings.TZ)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=tz)
    if isinstance(value, (int, float)) or _text(value).isdigit():
        try:
            number = float(value)
            return datetime.fromtimestamp(
                number / 1000 if number > 10_000_000_000 else number,
                tz=timezone.utc,
            ).astimezone(tz)
        except (OverflowError, OSError, ValueError):
            return None
    text = _text(value)
    for candidate in (text.replace("Z", "+00:00"),):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)
        except ValueError:
            pass
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=tz)
        except ValueError:
            continue
    return None


def _source_dicts(row: dict[str, Any]) -> list[dict[str, Any]]:
    sources = [row]
    for key in ("tradeOnline", "shopOrder", "order"):
        nested = row.get(key)
        if isinstance(nested, dict):
            sources.insert(0, nested)
    return sources


def _first(row: dict[str, Any], *keys: str) -> Any:
    for source in _source_dicts(row):
        for key in keys:
            value = source.get(key)
            if value is not None and value != "":
                return value
    return None


def _identity_keys(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    primary_keys = (
        "tradeNo", "orderNo", "order_no", "tradeId", "tid", "订单编号", "订单号",
        "线上订单编号", "原始订单号",
    )
    source_keys = ("sourceTradeNo", "source_trade_no", "网店订单号", "平台订单号")
    for key in primary_keys:
        value = _text(_first(row, key))
        if value and value not in values:
            values.append(value)

    source_order = _text(_first(row, *source_keys))
    platform = _text(_first(row, "shopName", "platform", "销售渠道", "店铺名称", "店铺"))
    if source_order:
        composite = f"{platform}:{source_order}" if platform else source_order
        if composite not in values:
            values.append(composite)
        if source_order not in values:
            values.append(source_order)
    if not values:
        raise JkyOrderNormalizationError("订单行缺少 tradeNo / tradeId / 平台订单号")
    return values


def _extract_item_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    for key in (
        "goodsDetail", "goodsDetailList", "tradeOnlineGoodsList", "items",
        "明细", "货品明细",
    ):
        value = _first(row, key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    # RPA/Excel 常见为一行一货品；只有确实出现货品列时才把订单行当明细。
    item_keys = (
        "goodsNo", "skuCode", "goodsName", "货品编号", "货品编码", "货品名称",
        "商品编号", "商品名称", "数量", "sellCount", "quantity",
    )
    if any(key in row and row.get(key) not in (None, "") for key in item_keys):
        return [row]
    return []


def _normalize_item(row: dict[str, Any]) -> dict[str, Any] | None:
    sku_code = _text(_first(row, "goodsNo", "skuCode", "sku_code", "货品编号", "货品编码", "商品编号"))
    goods_name = _text(_first(row, "goodsName", "skuName", "商品名称", "货品名称"))
    quantity = _decimal(_first(row, "sellCount", "quantity", "数量", "货品数量"))
    unit_price = _decimal(_first(row, "sellPrice", "price", "unitPrice", "单价"))
    amount = _decimal(_first(row, "sellTotal", "amount", "金额", "销售金额"))
    discount = _decimal(_first(row, "discountFee", "discount", "优惠"))
    if not any((sku_code, goods_name, quantity is not None, unit_price is not None, amount is not None)):
        return None
    return {
        "sku_code": sku_code,
        "goods_name": goods_name,
        "quantity": quantity,
        "unit_price": unit_price,
        "amount": amount,
        "discount_amount": discount,
        "raw": _json_safe(row),
    }


def normalize_order_row(row: dict[str, Any], source_provider: str) -> NormalizedJkyOrder:
    if not isinstance(row, dict):
        raise JkyOrderNormalizationError("订单行不是对象")
    identity_keys = _identity_keys(row)
    items = [item for raw_item in _extract_item_rows(row) if (item := _normalize_item(raw_item))]
    source_order_id = _text(_first(row, "tradeNo", "orderNo", "order_no", "tradeId", "订单编号", "订单号"))
    return NormalizedJkyOrder(
        order_no=identity_keys[0],
        source_provider=source_provider,
        source_order_id=source_order_id or identity_keys[0],
        identity_keys=identity_keys,
        platform=_text(_first(row, "shopName", "platform", "销售渠道", "店铺名称", "店铺")),
        order_type=_text(_first(row, "orderType", "tradeType", "订单类型")) or "trade",
        order_status=_text(_first(row, "tradeStatus", "orderStatus", "order_status", "status", "订单状态")),
        pay_status=_text(_first(row, "payStatus", "paymentStatus", "付款状态")),
        buyer_note=_text(_first(row, "buyerNote", "buyerMessage", "mergeRemarks", "买家留言", "合并备注")),
        order_amount=_decimal(_first(row, "orderAmount", "totalAmount", "payment", "应收合计", "订单金额")),
        paid_amount=_decimal(_first(row, "paidAmount", "realFee", "payment", "实付金额", "付款金额")),
        currency=_text(_first(row, "currency", "currencyCode", "币种")) or "CNY",
        ordered_at=_datetime(_first(row, "tradeTime", "createTime", "orderTime", "下单时间")),
        paid_at=_datetime(_first(row, "payTime", "paidAt", "付款时间")),
        items=items,
        raw=_json_safe(row),
    )


def _merge_order(target: NormalizedJkyOrder, incoming: NormalizedJkyOrder) -> None:
    target.identity_keys = list(dict.fromkeys(target.identity_keys + incoming.identity_keys))
    for field_name in (
        "platform", "order_type", "order_status", "pay_status", "buyer_note", "currency",
        "source_order_id",
    ):
        incoming_value = getattr(incoming, field_name)
        if incoming_value:
            setattr(target, field_name, incoming_value)
    for field_name in ("order_amount", "paid_amount", "ordered_at", "paid_at"):
        incoming_value = getattr(incoming, field_name)
        if incoming_value is not None:
            setattr(target, field_name, incoming_value)
    seen_items = {
        (
            item.get("sku_code", ""), item.get("goods_name", ""),
            str(item.get("quantity")), str(item.get("amount")),
        )
        for item in target.items
    }
    for item in incoming.items:
        key = (
            item.get("sku_code", ""), item.get("goods_name", ""),
            str(item.get("quantity")), str(item.get("amount")),
        )
        if key not in seen_items:
            target.items.append(item)
            seen_items.add(key)
    target.raw = incoming.raw or target.raw


def normalize_order_rows(
    rows: list[dict[str, Any]], source_provider: str
) -> tuple[list[NormalizedJkyOrder], int, list[str]]:
    """规范化并按任一身份别名合并同一订单。"""
    groups: list[NormalizedJkyOrder] = []
    alias_to_index: dict[str, int] = {}
    duplicate_count = 0
    invalid: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        try:
            order = normalize_order_row(row, source_provider)
        except JkyOrderNormalizationError as exc:
            invalid.append(f"第 {row_index} 行：{exc}")
            continue
        matching = sorted({alias_to_index[key] for key in order.identity_keys if key in alias_to_index})
        if not matching:
            group_index = len(groups)
            groups.append(order)
        else:
            group_index = matching[0]
            duplicate_count += 1
            _merge_order(groups[group_index], order)
            # 同一条记录桥接了两个旧分组时合并它们，避免一单两行。
            for other_index in reversed(matching[1:]):
                _merge_order(groups[group_index], groups[other_index])
                groups.pop(other_index)
                for key, index in list(alias_to_index.items()):
                    if index == other_index:
                        alias_to_index.pop(key)
                    elif index > other_index:
                        alias_to_index[key] = index - 1
        for key in groups[group_index].identity_keys:
            alias_to_index[key] = group_index
    return groups, duplicate_count, invalid


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _sync_window(db: Session) -> tuple[datetime, datetime]:
    now = datetime.now(ZoneInfo(settings.TZ)).replace(microsecond=0)
    checkpoint = load_checkpoint(db, JOB_PROVIDER, JOB_TYPE)
    previous_end = _datetime(checkpoint.get("windowEnd")) if isinstance(checkpoint, dict) else None
    if previous_end is None:
        start = now - timedelta(days=max(settings.JKY_ORDER_INITIAL_LOOKBACK_DAYS, 1))
    else:
        start = previous_end - timedelta(minutes=max(settings.JKY_ORDER_SYNC_OVERLAP_MINUTES, 0))
    return start, now


def _previous_fetched_count(db: Session) -> int:
    previous = (
        db.query(SyncJob)
        .filter_by(provider=JOB_PROVIDER, job_type=JOB_TYPE, status="success")
        .order_by(SyncJob.id.desc())
        .first()
    )
    if not previous:
        return 0
    value = (previous.stats or {}).get("normalizedCount", (previous.stats or {}).get("fetchedCount", 0))
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _start_job(db: Session) -> SyncJob:
    now = datetime.now(timezone.utc)
    running = (
        db.query(SyncJob)
        .filter_by(provider=JOB_PROVIDER, job_type=JOB_TYPE, status="running")
        .order_by(SyncJob.id.desc())
        .first()
    )
    if running:
        started_at = _aware(running.started_at)
        age = now - started_at if started_at else timedelta.max
        if age > timedelta(minutes=max(settings.JKY_ORDER_STALE_RUN_MINUTES, 1)):
            running.status = "failed"
            running.finished_at = now
            running.error_summary = "超过过期运行时长，已由新同步任务回收"
            db.add(SyncLog(
                provider=JOB_PROVIDER,
                level="error",
                sync_job_id=running.id,
                message="回收超时的吉客云订单同步任务",
                data={"staleRunMinutes": settings.JKY_ORDER_STALE_RUN_MINUTES},
            ))
            db.commit()
        else:
            raise JkyOrderSyncInProgress(f"已有同步任务运行（#{running.id}）")

    job = SyncJob(
        provider=JOB_PROVIDER,
        job_type=JOB_TYPE,
        status="running",
        started_at=now,
        stats={},
        error_summary="",
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise JkyOrderSyncInProgress("已有同步任务运行") from exc
    return job


def _channel_connection(db: Session, provider: str) -> IntegrationConnection:
    row = (
        db.query(IntegrationConnection)
        .filter_by(provider=provider, mode=CONNECTION_MODE)
        .first()
    )
    if row is None:
        row = IntegrationConnection(provider=provider, mode=CONNECTION_MODE, phase=1)
        db.add(row)
        db.flush()
    return row


def _set_channel_status(
    db: Session,
    health: JkyProviderHealth,
    *,
    status: str | None = None,
    message: str | None = None,
    verified: bool | None = None,
) -> None:
    row = _channel_connection(db, health.provider)
    row.status = status or health.status
    row.error_summary = (message if message is not None else health.message)[:500]
    row.last_tested_at = datetime.now(timezone.utc)
    if row.status == "connected":
        row.last_success_at = datetime.now(timezone.utc)
    row.meta = {
        **(row.meta or {}),
        "configured": health.configured,
        "verified": health.verified if verified is None else verified,
        "healthy": health.healthy,
        "label": CHANNEL_LABELS.get(health.provider, health.provider),
    }


def _log(db: Session, job: SyncJob, level: str, message: str, data: dict[str, Any] | None = None) -> None:
    db.add(SyncLog(
        provider=JOB_PROVIDER,
        level=level,
        sync_job_id=job.id,
        message=message,
        data=_json_safe(data or {}),
    ))


def _provider_priority() -> tuple[list[str], list[str]]:
    aliases = {"web": "jky_web", "rpa": "jky_rpa", "api": "jky_api", "openapi": "jky_api"}
    names: list[str] = []
    warnings: list[str] = []
    raw = settings.JKY_ORDER_PROVIDER_PRIORITY.strip() or "jky_web,jky_rpa,jky_api"
    for value in raw.split(","):
        key = aliases.get(value.strip().lower(), value.strip().lower())
        if key not in CHANNEL_LABELS:
            if value.strip():
                warnings.append(f"忽略未知吉客云订单通道：{value.strip()}")
            continue
        if key not in names:
            names.append(key)
    return names, warnings


def _upsert_orders(db: Session, orders: list[NormalizedJkyOrder]) -> dict[str, int]:
    created = 0
    updated = 0
    item_count = 0
    for order in orders:
        keys = list(dict.fromkeys(order.identity_keys + [order.order_no]))
        filters = [SalesOrder.order_no.in_(keys)]
        filters.extend(SalesOrder.identity_keys.contains([key]) for key in keys)
        matches = db.query(SalesOrder).filter(or_(*filters)).all()
        if len(matches) > 1:
            ids = ",".join(str(row.id) for row in matches)
            raise JkyOrderIdentityConflict(f"身份 {keys[0]} 同时命中本地订单 {ids}")
        row = matches[0] if matches else None
        if row is None:
            row = SalesOrder(order_no=order.order_no)
            db.add(row)
            db.flush()
            created += 1
        else:
            updated += 1

        for field_name in ("platform", "order_type", "order_status", "pay_status", "buyer_note", "currency"):
            value = getattr(order, field_name)
            if value:
                setattr(row, field_name, value)
        for field_name in ("order_amount", "paid_amount", "ordered_at", "paid_at"):
            value = getattr(order, field_name)
            if value is not None:
                setattr(row, field_name, value)

        current_keys = row.identity_keys if isinstance(row.identity_keys, list) else []
        row.identity_keys = list(dict.fromkeys([str(key) for key in current_keys] + keys))
        row.source_provider = order.source_provider
        row.source_order_id = order.source_order_id
        history = row.source_history if isinstance(row.source_history, list) else []
        history.append({
            "provider": order.source_provider,
            "orderId": order.source_order_id,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        row.source_history = history[-20:]
        raw = dict(order.raw or {})
        raw["_jky"] = {
            "sourceProvider": order.source_provider,
            "sourceOrderId": order.source_order_id,
            "identityKeys": row.identity_keys,
        }
        row.raw = _json_safe(raw)

        if order.items:
            db.query(SalesOrderItem).filter_by(order_id=row.id).delete(synchronize_session=False)
            for item in order.items:
                db.add(SalesOrderItem(order_id=row.id, **item))
            item_count += len(order.items)
    db.flush()
    return {"created": created, "updated": updated, "itemCount": item_count}


def _provider_error_status(exc: Exception) -> str:
    provider_status = getattr(exc, "status", "")
    if provider_status in {"needs_login", "needs_user_verify", "blocked", "unconfigured"}:
        return provider_status
    if isinstance(exc, AdapterNotConfigured):
        return "unconfigured"
    if isinstance(exc, AdapterPermissionError):
        return "blocked"
    if isinstance(exc, JkyWebError):
        if exc.kind == "need_login":
            return "needs_login"
        if exc.kind == "need_user_verify":
            return "needs_user_verify"
        if exc.kind == "unconfigured":
            return "unconfigured"
    return "error"


def sync_orders(db: Session, actor: str = "system") -> dict[str, Any]:
    """按配置优先级执行 Web → RPA → API，并只由成功通道推进游标。"""
    try:
        job = _start_job(db)
    except JkyOrderSyncInProgress as exc:
        return {"status": "already_running", "message": str(exc)}

    start, end = _sync_window(db)
    previous_count = _previous_fetched_count(db)
    provider_names, priority_warnings = _provider_priority()
    providers = build_order_providers(db)
    attempts: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = [
        {"code": "invalid_priority", "message": item, "blocksCursor": False}
        for item in priority_warnings
    ]
    result_stats: dict[str, Any] = {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "priority": provider_names,
        "previousFetchedCount": previous_count,
        "providerAttempts": attempts,
    }

    try:
        for provider_name in provider_names:
            provider = providers[provider_name]
            try:
                health = provider.health_check()
            except Exception as exc:  # 健康检查本身失败也必须进入下一通道
                status = _provider_error_status(exc)
                health = JkyProviderHealth(
                    provider_name,
                    status,
                    status not in {"unconfigured", "needs_login"},
                    False,
                    False,
                    str(exc)[:500],
                )
            _set_channel_status(db, health)
            attempt: dict[str, Any] = {
                "provider": provider_name,
                "label": CHANNEL_LABELS[provider_name],
                "health": health.status,
                "verified": health.verified,
            }
            attempts.append(attempt)
            if not health.configured or not health.healthy:
                attempt.update({"status": "skipped", "message": health.message})
                _log(db, job, "warn", f"{CHANNEL_LABELS[provider_name]} 跳过：{health.message}", {"provider": provider_name})
                continue

            try:
                rows = provider.fetch_order_rows(start, end)
                normalized, duplicate_count, invalid = normalize_order_rows(rows, provider_name)
                attempt["fetchedCount"] = len(rows)
                attempt["normalizedCount"] = len(normalized)
                attempt["duplicateCount"] = duplicate_count
                if invalid:
                    attempt.update({"status": "invalid", "invalidCount": len(invalid), "errors": invalid[:10]})
                    warnings.append({
                        "code": "invalid_orders",
                        "message": f"{CHANNEL_LABELS[provider_name]} 有 {len(invalid)} 行无法确认订单身份",
                        "blocksCursor": True,
                    })
                    _set_channel_status(db, health, status="error", message=attempt["message"] if "message" in attempt else "订单身份校验失败", verified=False)
                    _log(db, job, "error", f"{CHANNEL_LABELS[provider_name]} 返回了无法确认身份的订单行", {"errors": invalid[:10]})
                    continue

                if (
                    previous_count >= 10
                    and len(normalized) > 0
                    and len(normalized) < max(1, int(previous_count * settings.JKY_ORDER_LOW_COUNT_RATIO))
                ):
                    message = (
                        f"{CHANNEL_LABELS[provider_name]} 本次确认 {len(normalized)} 单，"
                        f"低于上次 {previous_count} 单的安全阈值，未推进游标"
                    )
                    attempt.update({"status": "low_count", "message": message})
                    warnings.append({"code": "unexpectedly_low_count", "message": message, "blocksCursor": True})
                    _set_channel_status(db, health, status="error", message=message, verified=False)
                    _log(db, job, "error", message, {"provider": provider_name})
                    continue

                upsert_stats = _upsert_orders(db, normalized)
                attempt["status"] = "success"
                result_stats.update({
                    "provider": provider_name,
                    "fetchedCount": len(rows),
                    "normalizedCount": len(normalized),
                    "duplicateCount": duplicate_count,
                    "invalidCount": 0,
                    **upsert_stats,
                    "cursorAdvanceAllowed": True,
                    "warnings": warnings,
                })
                _set_channel_status(
                    db,
                    health,
                    status="connected",
                    message="本次订单同步已验证并写入统一订单库",
                    verified=True,
                )
                _log(db, job, "info", f"{CHANNEL_LABELS[provider_name]} 订单同步完成", result_stats)
                checkpoint = (
                    db.query(SyncCheckpoint)
                    .filter_by(provider=JOB_PROVIDER, job_type=JOB_TYPE)
                    .first()
                )
                if checkpoint is None:
                    checkpoint = SyncCheckpoint(provider=JOB_PROVIDER, job_type=JOB_TYPE, checkpoint={})
                    db.add(checkpoint)
                checkpoint.checkpoint = {
                    "windowStart": start.isoformat(),
                    "windowEnd": end.isoformat(),
                    "provider": provider_name,
                    "updatedAt": datetime.now(timezone.utc).isoformat(),
                }
                finish_sync_job(db, job, "success", result_stats)
                # 同步恢复：自动关闭历史吉客云同步故障告警（ensure_exception 的对称操作）
                from app.services.integration_service import resolve_exception

                resolve_exception(db, "JACKYUN_SYNC_FAIL", f"订单同步恢复（job #{job.id}，通道 {provider_name}），自动关闭")
                return {"status": "success", "jobId": job.id, "stats": result_stats, "actor": actor}
            except Exception as exc:  # 该通道失败后进入下一通道
                db.rollback()
                status = _provider_error_status(exc)
                attempt.update({"status": status, "error": str(exc)[:500]})
                error_health = JkyProviderHealth(
                    provider_name, status, health.configured, False, False, str(exc)[:500]
                )
                _set_channel_status(db, error_health, status=status, message=str(exc), verified=False)
                _log(db, job, "error", f"{CHANNEL_LABELS[provider_name]} 失败，准备切换下一通道：{exc}", {"provider": provider_name, "status": status})

        configured_attempts = [item for item in attempts if item.get("health") not in {"unconfigured", "needs_login"}]
        result_stats.update({
            "status": "failed" if configured_attempts else "skipped",
            "cursorAdvanceAllowed": False,
            "warnings": warnings,
            "errors": [item.get("error") or item.get("message") for item in attempts if item.get("status") not in {"skipped", "success"}],
        })
        finish_sync_job(
            db,
            job,
            "failed" if configured_attempts else "skipped",
            result_stats,
            "；".join(str(item) for item in result_stats["errors"] if item)[:500],
        )
        return {"status": result_stats["status"], "jobId": job.id, "stats": result_stats, "actor": actor}
    except Exception as exc:
        db.rollback()
        job = db.get(SyncJob, job.id)
        if job and job.status == "running":
            finish_sync_job(db, job, "failed", {"cursorAdvanceAllowed": False}, str(exc)[:500])
        return {"status": "failed", "jobId": job.id if job else None, "error": str(exc)[:500]}


def _configured(provider: str, db: Session) -> tuple[bool, str]:
    if provider == "jky_web":
        from app.adapters.jky_web import JkyWebSessionManager

        return bool(settings.JKY_WEB_SIGN_SECRET and JkyWebSessionManager(db).has_auth()), "网页登录态或签名密钥未配置"
    if provider == "jky_rpa":
        return bool(settings.JKY_RPA_AGENT_URL and settings.JKY_RPA_AGENT_TOKEN), "Windows RPA Agent 未配置"
    if provider == "jky_api":
        return settings.jackyun_configured, "吉客云 API/MCP 凭证未配置"
    return False, "未知通道"


def order_sync_status(db: Session) -> dict[str, Any]:
    """只读本地配置、状态和同步日志，不触发外部通道请求。"""
    priority, priority_warnings = _provider_priority()
    channels: list[dict[str, Any]] = []
    for provider in CHANNEL_LABELS:
        configured, not_configured_message = _configured(provider, db)
        connection = (
            db.query(IntegrationConnection)
            .filter_by(provider=provider, mode=CONNECTION_MODE)
            .first()
        )
        status = connection.status if connection else ("configured_not_tested" if configured else "unconfigured")
        if not configured and status not in {"needs_login", "needs_user_verify", "error", "blocked"}:
            status = "unconfigured"
        channels.append({
            "provider": provider,
            "label": CHANNEL_LABELS[provider],
            "priority": priority.index(provider) + 1 if provider in priority else None,
            "configured": configured,
            "status": status,
            "verified": bool((connection.meta or {}).get("verified")) if connection else False,
            "lastTestedAt": connection.last_tested_at.isoformat() if connection and connection.last_tested_at else None,
            "lastSuccessAt": connection.last_success_at.isoformat() if connection and connection.last_success_at else None,
            "errorSummary": (connection.error_summary or not_configured_message) if connection else (None if configured else not_configured_message),
        })
    last_job = (
        db.query(SyncJob)
        .filter_by(provider=JOB_PROVIDER, job_type=JOB_TYPE)
        .order_by(SyncJob.id.desc())
        .first()
    )
    checkpoint = load_checkpoint(db, JOB_PROVIDER, JOB_TYPE)
    return {
        "providerPriority": priority,
        "priorityWarnings": priority_warnings,
        "channels": channels,
        "lastRun": {
            "id": last_job.id if last_job else None,
            "status": last_job.status if last_job else None,
            "startedAt": last_job.started_at.isoformat() if last_job and last_job.started_at else None,
            "finishedAt": last_job.finished_at.isoformat() if last_job and last_job.finished_at else None,
            "stats": (last_job.stats or {}) if last_job else {},
            "errorSummary": last_job.error_summary if last_job else "",
        },
        "checkpoint": checkpoint,
    }
