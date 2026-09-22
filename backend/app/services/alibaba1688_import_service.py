"""1688 官方订单导出文件的本地受管导入。"""

from __future__ import annotations

import hashlib
import math
import mimetypes
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.alibaba1688_export_file import ParsedAlibaba1688Export, parse_alibaba1688_export
from app.adapters.bank_file import sanitize_name
from app.config import settings
from app.core.audit import audit
from app.models.alibaba1688_import import Alibaba1688FileImport, Alibaba1688Order
from app.models.purchase import ExternalPurchaseOrder
from app.services.inbound_allocation_seed import seed_allocations_for_order_numbers
from app.services.import_lifecycle import filter_lifecycle, transition_lifecycle, transition_row_status


def _root() -> Path:
    root = Path(settings.DATA_DIR).resolve() / "alibaba1688-exports"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _store_new_file(content: bytes, original_name: str, sha256: str) -> tuple[Path, bool]:
    target = _root() / sha256[:2] / f"{sha256}_{sanitize_name(original_name)}"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("xb") as output:
            output.write(content)
        return target, True
    except FileExistsError:
        return target, False


def _json_safe(value: Any) -> Any:
    """JSONB 无法序列化 Decimal/datetime：统一转字符串（浏览器直采的映射结果会带这两种类型）。"""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _json_safe_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {key: _json_safe(value) for key, value in data.items()}


def _to_decimal(value: Any) -> Decimal:
    text = str(value or "").strip().replace(",", "").replace("，", "")
    if not text or text in {"-", "--", "/"}:
        return Decimal("0")
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _to_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("/", "-")
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # XLSX 数字日期使用 1899-12-30 起算的 Excel serial day（含小数时间部分）。
    try:
        serial = float(text)
        if math.isfinite(serial) and 1 <= serial <= 100000:
            return datetime(1899, 12, 30, tzinfo=timezone.utc) + timedelta(days=serial)
    except ValueError:
        pass
    return None


def _sync_purchase_workflow_order(
    db: Session, data: dict[str, Any], *, source: str = "alibaba1688_file"
) -> bool:
    """把订单标准字段同步到采购工作流表，保持采购页与链路页使用同一订单号。

    ``Alibaba1688Order`` 保存来源标准字段，``ExternalPurchaseOrder`` 保存
    SKU 分配、状态流转和吉客云采购单关联；这里仅同步来源字段，不覆盖人工维护的
    采购内容和状态。``source`` 只写入 raw 标记数据来自哪个通道（file/browser）。
    """
    external_order_id = str(data.get("external_order_id") or "").strip()
    if not external_order_id:
        return False
    supplier = str(data.get("seller_company_name") or "").strip()
    buyer = str(data.get("buyer_member_name") or data.get("buyer_company_name") or "").strip()
    order_status = str(data.get("order_status") or "").strip()
    order_remark = str(data.get("order_remark") or "").strip()
    ordered_at = _to_datetime(data.get("order_time"))
    paid_amount = _to_decimal(data.get("actual_payment")) if data.get("actual_payment") not in (None, "") else None
    goods_total = _to_decimal(data.get("goods_total")) if data.get("goods_total") not in (None, "") else None
    freight = _to_decimal(data.get("freight")) if data.get("freight") not in (None, "") else None
    discount = _to_decimal(data.get("discount")) if data.get("discount") not in (None, "") else None
    logistics = data.get("logistics") if isinstance(data.get("logistics"), dict) else {}
    order_amount = None
    if goods_total is not None or freight is not None or discount is not None:
        order_amount = (goods_total or Decimal("0")) + (freight or Decimal("0")) - (discount or Decimal("0"))
    now = datetime.now(timezone.utc)
    raw = {"source": source, **_json_safe_dict(data)}

    from app.services.supplier_sync_service import ensure_supplier
    ensure_supplier(db, supplier, platform="1688")

    # 1688 来源只能更新 1688 工作流副本；其他渠道可能存在相同订单号。
    row = db.query(ExternalPurchaseOrder).filter_by(
        platform="1688",
        external_order_id=external_order_id,
    ).first()
    if row is None:
        db.add(ExternalPurchaseOrder(
            external_order_id=external_order_id,
            platform="1688",
            buyer_account=buyer,
            supplier_name=supplier,
            title="",
            ordered_at=ordered_at,
            order_amount=order_amount,
            paid_amount=paid_amount,
            order_status=order_status,
            logistics=logistics,
            synced_at=now,
            raw=raw,
        ))
        return True

    changed = False
    if supplier and row.supplier_name != supplier:
        row.supplier_name = supplier
        changed = True
    if buyer and row.buyer_account != buyer:
        row.buyer_account = buyer
        changed = True
    if order_status and row.order_status != order_status:
        row.order_status = order_status
        changed = True
    if order_remark and (row.raw or {}).get("order_remark") != order_remark:
        # ExternalPurchaseOrder 没有独立备注列，工作流副本随 raw 一起保留。
        changed = True
    if ordered_at is not None and row.ordered_at != ordered_at:
        row.ordered_at = ordered_at
        changed = True
    if order_amount is not None and row.order_amount != order_amount:
        row.order_amount = order_amount
        changed = True
    if paid_amount is not None and row.paid_amount != paid_amount:
        row.paid_amount = paid_amount
        changed = True
    if logistics:
        merged_logistics = {**(row.logistics or {}), **logistics}
        if row.logistics != merged_logistics:
            row.logistics = merged_logistics
            changed = True
    merged_raw = {**(row.raw or {}), **raw}
    if row.raw != merged_raw:
        row.raw = merged_raw
        changed = True
    if row.synced_at != now:
        row.synced_at = now
        changed = True
    return changed


def _merge_source_order(
    order: Alibaba1688Order,
    data: dict[str, Any],
    *,
    source: str = "alibaba1688_file",
    update_status: bool = False,
) -> bool:
    """把再次出现的订单字段补回来源副本，不改动采购工作流字段。

    默认保守：只补齐缺失字段（Excel 重复导入语义）。
    ``update_status=True``（浏览器直采）额外刷新 ``order_status``——
    页面上订单状态是活的（如 已付款 → 已发货），不应停留在首次抓取值。
    """
    changed = False
    if update_status:
        new_status = str(data.get("order_status") or "").strip()
        if new_status and (order.order_status or "") != new_status:
            order.order_status = new_status
            changed = True
    for attr, key in (
        ("order_time", "order_time"),
        ("pay_time", "pay_time"),
    ):
        if getattr(order, attr) is None and data.get(key) not in (None, ""):
            value = _to_datetime(data.get(key))
            if value is not None:
                setattr(order, attr, value)
                changed = True
    new_remark = str(data.get("order_remark") or "").strip()
    if new_remark and (order.order_remark or "") != new_remark:
        order.order_remark = new_remark
        changed = True
    for attr, key in (
        ("goods_total", "goods_total"),
        ("freight", "freight"),
        ("discount", "discount"),
        ("actual_payment", "actual_payment"),
    ):
        if data.get(key) in (None, ""):
            continue
        value = _to_decimal(data.get(key))
        if getattr(order, attr) in (None, Decimal("0")) and value != Decimal("0"):
            setattr(order, attr, value)
            changed = True
    merged_raw = {**(order.raw_payload or {}), **{"source": source, **_json_safe_dict(data)}}
    if order.raw_payload != merged_raw:
        order.raw_payload = merged_raw
        changed = True
    return changed


def upsert_order_data(
    db: Session,
    data: dict[str, Any],
    *,
    import_id: int,
    source: str = "alibaba1688_file",
    adopt_from_deleted: bool = True,
    update_status: bool = False,
    restore_deleted: bool = False,
) -> str:
    """把一条标准字段订单 upsert 到来源表 + 采购工作流表（Excel 导入与浏览器直采共用）。

    返回值：
    - ``created``         新订单（来源表 + 工作流都新建）
    - ``adopted``         已有订单从回收站批次重新挂到当前批次
    - ``merged``          已有订单刷新状态/时间/金额（浏览器直采会更新 order_status）
    - ``restored`` 用户明确按订单号补拉，恢复之前软删除的订单
    - ``skipped_deleted`` 用户在明细核对中逐行删除过的订单，不自动复活
    - ``skipped_no_id``   缺订单号，无法处理
    """
    external_order_id = str(data.get("external_order_id") or "").strip()
    if not external_order_id:
        return "skipped_no_id"
    existing = db.query(Alibaba1688Order).filter_by(
        external_order_id=external_order_id
    ).first()
    if existing is not None:
        if existing.row_status != "deleted":
            adopted = False
            if adopt_from_deleted:
                previous_import = db.get(Alibaba1688FileImport, existing.import_id)
                if previous_import is not None and previous_import.lifecycle == "deleted":
                    # 同一订单再次出现且当前批次生效时，让来源副本跟随当前有效批次；
                    # 否则采购工作流会失去有效 1688 源订单。
                    existing.import_id = import_id
                    adopted = True
            _merge_source_order(existing, data, source=source, update_status=update_status)
            _sync_purchase_workflow_order(db, data, source=source)
            return "adopted" if adopted else "merged"
        if restore_deleted:
            # 只有显式的单号补拉才允许恢复；挂到当前有效批次，重新进入采购工作台。
            existing.row_status = "active"
            existing.import_id = import_id
            _merge_source_order(existing, data, source=source, update_status=update_status)
            _sync_purchase_workflow_order(db, data, source=source)
            return "restored"
        # 用户明确删除过的行不随重复导入自动回到工作流；保留来源字段补全语义。
        return "skipped_deleted"
    db.add(Alibaba1688Order(
        import_id=import_id,
        external_order_id=external_order_id,
        buyer_company_name=str(data.get("buyer_company_name") or ""),
        buyer_member_name=str(data.get("buyer_member_name") or ""),
        seller_company_name=str(data.get("seller_company_name") or ""),
        seller_member_name=str(data.get("seller_member_name") or ""),
        goods_total=_to_decimal(data.get("goods_total")),
        freight=_to_decimal(data.get("freight")),
        discount=_to_decimal(data.get("discount")),
        actual_payment=_to_decimal(data.get("actual_payment")),
        order_status=str(data.get("order_status") or ""),
        order_time=_to_datetime(data.get("order_time")),
        pay_time=_to_datetime(data.get("pay_time")),
        order_remark=str(data.get("order_remark") or "").strip(),
        raw_payload={"source": source, **_json_safe_dict(data)},
    ))
    _sync_purchase_workflow_order(db, data, source=source)
    # autoflush=False 的会话里，同一次导入/同步重复出现同一订单号时，
    # 未 flush 的 pending 行对后续查询不可见，会在唯一约束上撞车。
    db.flush()
    return "created"


def _backfill_order_times(db: Session, content: bytes, original_name: str) -> None:
    """同一文件重复导入时，仅补齐已有订单缺失的下单/付款时间，不改动其他字段。"""
    parsed = parse_alibaba1688_export(content, original_name, max_rows=settings.MAX_JACKYUN_IMPORT_ROWS)
    if parsed.status != "parsed":
        return
    updated = 0
    workflow_updated = False
    for data in parsed.rows:
        external_order_id = str(data.get("external_order_id") or "").strip()
        if not external_order_id:
            continue
        order = db.query(Alibaba1688Order).filter_by(external_order_id=external_order_id).first()
        if order is None or order.row_status == "deleted":
            # 已被用户删除的行不参与回填，避免删除后又被重复导入「复活」。
            continue
        changed = _merge_source_order(order, data)
        if changed:
            updated += 1
        workflow_updated = _sync_purchase_workflow_order(db, data) or workflow_updated
    if updated or workflow_updated:
        db.commit()


def _adopt_orders_from_deleted_imports(
    db: Session,
    target_import: Alibaba1688FileImport,
    rows: list[dict[str, Any]],
) -> tuple[int, int]:
    """把当前生效文件中的同单号来源行从已删除批次迁入当前批次。

    1688 源订单以订单号全局去重。若旧导入已进回收站、用户又上传同一订单，
    单纯补字段会让工作流仍指向已删除的来源。这里只迁移 ``active`` 行；
    用户逐行删除的订单始终不自动复活。
    """
    if target_import.lifecycle != "active":
        return 0, 0

    adopted = 0
    skipped_deleted = 0
    for data in rows:
        external_order_id = str(data.get("external_order_id") or "").strip()
        if not external_order_id:
            continue
        source = db.query(Alibaba1688Order).filter_by(external_order_id=external_order_id).first()
        if source is None:
            continue
        if source.row_status == "deleted":
            skipped_deleted += 1
            continue
        if source.import_id == target_import.id:
            continue
        previous_import = db.get(Alibaba1688FileImport, source.import_id)
        if previous_import is None or previous_import.lifecycle != "deleted":
            continue
        source.import_id = target_import.id
        _merge_source_order(source, data)
        _sync_purchase_workflow_order(db, data)
        adopted += 1
    return adopted, skipped_deleted


def reconcile_active_import(
    db: Session,
    import_id: int,
    *,
    actor: str,
) -> tuple[Alibaba1688FileImport, dict[str, int]]:
    """修复生效导入与其订单来源副本的归属，不恢复已删除批次或已删明细。"""
    row = db.get(Alibaba1688FileImport, import_id)
    if row is None:
        raise LookupError(f"alibaba1688_file_imports #{import_id} 不存在")
    if row.lifecycle != "active":
        raise ValueError("只有 active 状态的 1688 导入可以修复订单归属")

    path = Path(row.stored_path)
    if not path.is_file():
        raise ValueError("找不到该导入的原始文件，无法安全修复订单归属")
    parsed = parse_alibaba1688_export(
        path.read_bytes(), row.original_name, max_rows=settings.MAX_JACKYUN_IMPORT_ROWS
    )
    if parsed.status != "parsed":
        raise ValueError(f"解析失败：{parsed.error_summary or '未找到有效订单'}")

    adopted, skipped_deleted = _adopt_orders_from_deleted_imports(db, row, parsed.rows)
    previous_count = row.imported_order_count
    # 运行中的服务关闭了 autoflush；先落入当前事务，再读取本批次实际归属数。
    db.flush()
    current_count = db.query(Alibaba1688Order).filter_by(import_id=row.id).count()
    row.imported_order_count = current_count
    changed = adopted > 0 or previous_count != current_count
    if changed:
        db.commit()
        audit(
            db,
            actor,
            "alibaba1688.file_import.reconcile_source_orders",
            "alibaba1688_file_imports",
            row.id,
            {
                "adoptedOrders": adopted,
                "skippedDeletedRows": skipped_deleted,
                "orderCount": current_count,
            },
        )
    db.refresh(row)
    return row, {
        "adoptedOrders": adopted,
        "skippedDeletedRows": skipped_deleted,
        "orderCount": current_count,
    }


def serialize_import(row: Alibaba1688FileImport) -> dict:
    return {
        "id": row.id,
        "fileName": row.original_name,
        "fileHash": row.sha256[:16],
        "fileSize": row.size,
        "orderCount": row.imported_order_count,
        "status": row.status,
        "lifecycle": row.lifecycle,
        "lifecycleChangedAt": row.lifecycle_changed_at.isoformat() if row.lifecycle_changed_at else None,
        "errorMessage": row.error_summary or None,
        "uploader": row.uploader,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def import_export(
    db: Session,
    *,
    content: bytes,
    original_name: str,
    actor: str,
    auto_confirm: bool = False,
) -> tuple[Alibaba1688FileImport, bool]:
    """保存原件并将订单标准字段写入本地；相同文件指纹不会重复导入。

    默认进入 ``draft`` 暂存，需要在 UI 确认后才会出现在采购链路/工作台；
    ``auto_confirm=True`` 时直接落地为 ``active``，用于自动化场景。
    """
    if not content:
        raise ValueError("空文件")
    original_name = original_name or "1688-orders.xlsx"
    if len(content) > settings.MAX_UPLOAD_BYTES:
        limit = settings.MAX_UPLOAD_BYTES // (1024 * 1024)
        raise ValueError(f"1688 导出文件超过单文件上限（{limit} MiB）")

    sha256 = hashlib.sha256(content).hexdigest()
    existing = db.query(Alibaba1688FileImport).filter_by(sha256=sha256).first()
    if existing:
        _backfill_order_times(db, content, original_name)
        return existing, True

    parsed: ParsedAlibaba1688Export = parse_alibaba1688_export(
        content, original_name, max_rows=settings.MAX_JACKYUN_IMPORT_ROWS
    )
    if parsed.status != "parsed":
        raise ValueError(f"解析失败：{parsed.error_summary or '未找到有效订单'}")

    target, created_file = _store_new_file(content, original_name, sha256)
    now = datetime.now(timezone.utc)
    initial_lifecycle = "active" if auto_confirm else "draft"
    row = Alibaba1688FileImport(
        original_name=original_name,
        stored_path=str(target),
        sha256=sha256,
        size=len(content),
        mime=mimetypes.guess_type(original_name)[0] or "application/octet-stream",
        status="completed",
        sheet_name=parsed.sheet_name,
        headers=parsed.headers,
        row_count=len(parsed.rows),
        imported_order_count=0,
        error_summary=parsed.error_summary or "",
        uploader=actor,
        lifecycle=initial_lifecycle,
        lifecycle_changed_at=now,
        parsed_at=now,
    )
    imported_count = 0
    imported_order_numbers: set[str] = set()
    try:
        db.add(row)
        db.flush()
        for data in parsed.rows:
            external_order_id = str(data.get("external_order_id") or "").strip()
            if not external_order_id:
                continue
            imported_order_numbers.add(external_order_id)
            outcome = upsert_order_data(
                db, data,
                import_id=row.id,
                source="alibaba1688_file",
                # draft 暂存批次不抢占已有订单归属；确认生效时由 reconcile 迁移。
                adopt_from_deleted=initial_lifecycle == "active",
            )
            if outcome in ("created", "adopted"):
                imported_count += 1
        row.imported_order_count = imported_count
        db.commit()
        # 兼容历史顺序：入库对照可能先于工作流 PO 存在，导入完成后补齐 SKU 行。
        seed_allocations_for_order_numbers(db, imported_order_numbers)
        if initial_lifecycle == "active":
            from app.services.alibaba1688_remark_match_service import run_verified_remark_match

            run_verified_remark_match(db, actor=actor)
    except IntegrityError:
        db.rollback()
        existing = db.query(Alibaba1688FileImport).filter_by(sha256=sha256).first()
        if existing:
            return existing, True
        if created_file:
            target.unlink(missing_ok=True)
        raise
    except Exception:
        db.rollback()
        if created_file:
            target.unlink(missing_ok=True)
        raise

    audit(
        db,
        actor,
        "alibaba1688.file_import.upload",
        "alibaba1688_file_imports",
        row.id,
        {"rows": row.row_count, "orders": row.imported_order_count, "sha256": row.sha256[:16]},
    )
    return row, False


def list_imports(db: Session, lifecycle: str | None = None, limit: int = 50) -> list[dict]:
    query = db.query(Alibaba1688FileImport).order_by(Alibaba1688FileImport.id.desc())
    query = filter_lifecycle(query, Alibaba1688FileImport, lifecycle)
    rows = query.limit(min(max(limit, 1), 200)).all()
    return [serialize_import(row) for row in rows]


def confirm_import(db: Session, import_id: int, actor: str) -> Alibaba1688FileImport:
    row = transition_lifecycle(
        db, Alibaba1688FileImport, import_id,
        target="active", allowed_from=("draft",),
        actor=actor, audit_action="alibaba1688.file_import.confirm",
    )
    # 兼容历史迁移记录：早期手工生成的导入行可能没有保留原始文件，
    # 这类记录仍可确认，只是不执行需要原件的归属修复。
    if Path(row.stored_path).is_file():
        reconcile_active_import(db, row.id, actor=actor)
    from app.services.alibaba1688_remark_match_service import run_verified_remark_match

    run_verified_remark_match(db, actor=actor)
    return row


def soft_delete_import(db: Session, import_id: int, actor: str) -> Alibaba1688FileImport:
    return transition_lifecycle(
        db, Alibaba1688FileImport, import_id,
        target="deleted", allowed_from=("draft", "active"),
        actor=actor, audit_action="alibaba1688.file_import.soft_delete",
    )


def restore_import(db: Session, import_id: int, actor: str) -> Alibaba1688FileImport:
    return transition_lifecycle(
        db, Alibaba1688FileImport, import_id,
        target="draft", allowed_from=("deleted",),
        actor=actor, audit_action="alibaba1688.file_import.restore",
    )


def serialize_order(order: Alibaba1688Order) -> dict:
    return {
        "id": order.id,
        "externalOrderId": order.external_order_id,
        "buyerCompanyName": order.buyer_company_name,
        "buyerMemberName": order.buyer_member_name,
        "sellerCompanyName": order.seller_company_name,
        "sellerMemberName": order.seller_member_name,
        "goodsTotal": str(order.goods_total),
        "freight": str(order.freight),
        "discount": str(order.discount),
        "actualPayment": str(order.actual_payment),
        "orderStatus": order.order_status,
        "orderTime": order.order_time.isoformat() if order.order_time else None,
        "payTime": order.pay_time.isoformat() if order.pay_time else None,
        "orderRemark": order.order_remark or "",
        "rowStatus": order.row_status,
    }


def delete_order_row(db: Session, import_id: int, order_id: int, actor: str) -> Alibaba1688Order:
    """明细核对：删除该导入下的单个订单行（可恢复）；业务查询同步排除。"""
    exists = (
        db.query(Alibaba1688Order.id)
        .filter_by(id=order_id, import_id=import_id)
        .first()
    )
    if exists is None:
        raise LookupError(f"订单 #{order_id} 不属于导入 #{import_id}")
    return transition_row_status(
        db, Alibaba1688Order,
        lookup={"id": order_id, "import_id": import_id},
        target="deleted", actor=actor, audit_action="alibaba1688.file_import.delete_order_row",
    )


def restore_order_row(db: Session, import_id: int, order_id: int, actor: str) -> Alibaba1688Order:
    exists = (
        db.query(Alibaba1688Order.id)
        .filter_by(id=order_id, import_id=import_id)
        .first()
    )
    if exists is None:
        raise LookupError(f"订单 #{order_id} 不属于导入 #{import_id}")
    return transition_row_status(
        db, Alibaba1688Order,
        lookup={"id": order_id, "import_id": import_id},
        target="active", actor=actor, audit_action="alibaba1688.file_import.restore_order_row",
    )


def get_import_detail(db: Session, import_id: int) -> dict | None:
    row = db.query(Alibaba1688FileImport).filter_by(id=import_id).first()
    if row is None:
        return None
    # 已删除行也返回（带 rowStatus），前端置灰展示并提供恢复入口。
    orders = (
        db.query(Alibaba1688Order)
        .filter_by(import_id=import_id)
        .order_by(Alibaba1688Order.order_time.desc().nullslast(), Alibaba1688Order.id.desc())
        .all()
    )
    result = serialize_import(row)
    result["orders"] = [serialize_order(order) for order in orders]
    return result
