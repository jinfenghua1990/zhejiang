"""入库单明细 → SKU 分配反填。

链路建链确认后（manual/replace/xref apply/prelink auto），调用本服务：
按已确认关联的 RK 明细（matched_sku_id 非空）为对应 PO 创建 PurchaseAllocationItem
"待分配"行（quantity/price 从 RK 明细取）。已存在的同 (po_id, sku_id) 行不会被替换
（人工填优先），保证幂等。

只在 PO 处于可编辑状态（pending_refine / draft）时执行；已 refine/locked 的不再动。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.models.alibaba1688_import import Alibaba1688Order
from app.models.catalog import ProductSku
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.models.ops import ExceptionRecord
from app.models.procurement_chain import ProcurementChainLink
from app.models.purchase import (
    ExternalPurchaseOrder,
    InboundLink,
    PurchaseAllocationItem,
)
from app.services.allocation import balance_check


SEEDABLE_PO_STATUSES = ("pending_refine", "draft")


def _payment_gap(
    po: ExternalPurchaseOrder,
    allocation_total: Decimal,
) -> Decimal | None:
    """Return an unconfirmed difference between inbound amount and 1688 paid."""
    if (po.platform or "1688").lower() != "1688":
        return None
    paid = po.effective_paid_amount
    if allocation_total <= 0 or paid is None:
        return None
    gap = (allocation_total - paid).quantize(Decimal("0.0001"))
    return gap if abs(gap) > Decimal("0.01") else None


def _ensure_payment_gap_exception(
    db: Session,
    po: ExternalPurchaseOrder,
    allocation_total: Decimal,
    gap: Decimal,
) -> None:
    """Put an amount difference in the exception center without auto-approving it."""
    detail = {
        "poId": po.id,
        "orderId": po.id,
        "externalOrderId": po.external_order_id,
        "paidAmount": str(po.effective_paid_amount),
        "inboundAmount": str(allocation_total),
        "difference": str(gap),
        "suggestedAdjustment": str(gap),
        "reason": "实际入库金额与 1688 实际付款金额不一致，可能包含红包/优惠",
        "actionRequired": "请确认后再继续采购流程",
    }
    row = db.query(ExceptionRecord).filter(
        ExceptionRecord.code == "PURCHASE_PAYMENT_GAP",
        ExceptionRecord.ref_table == "external_purchase_orders",
        ExceptionRecord.ref_id == str(po.id),
        ExceptionRecord.status.in_(("pending", "confirmed")),
    ).order_by(ExceptionRecord.id.desc()).first()
    if row is None:
        db.add(ExceptionRecord(
            code="PURCHASE_PAYMENT_GAP",
            type="PURCHASE_PAYMENT_GAP",
            severity="medium",
            title="1688 实付与入库金额差异待确认",
            detail=detail,
            status="pending",
            source="system",
            ref_table="external_purchase_orders",
            ref_id=str(po.id),
        ))
    elif row.status == "pending":
        row.detail = detail
    db.commit()


def _po_from_link(db: Session, link: ProcurementChainLink) -> ExternalPurchaseOrder | None:
    """从一条 inbound 链路反向定位 PO：先看链本身的 external_po_id / order_id，
    再按 external_order_id 兜底找 workflow 副本。"""
    if link.target_type != "inbound":
        return None
    if link.external_po_id:
        return db.get(ExternalPurchaseOrder, link.external_po_id)
    if link.order_id:
        order = db.get(Alibaba1688Order, link.order_id)
        if order and order.external_order_id:
            return (
                db.query(ExternalPurchaseOrder)
                .filter_by(platform="1688", external_order_id=order.external_order_id)
                .first()
            )
    return None


def collect_linked_doc_ids_for_po(db: Session, po: ExternalPurchaseOrder) -> set[int]:
    """汇总该 PO 已确认关联的入库单 ID：含 procurement_chain_links 与旧 InboundLink。"""
    doc_ids: set[int] = set()
    if (po.platform or "").lower() == "1688" and po.external_order_id:
        order = db.query(Alibaba1688Order).filter_by(external_order_id=po.external_order_id).first()
        if order:
            for l in db.query(ProcurementChainLink).filter(
                ProcurementChainLink.order_id == order.id,
                ProcurementChainLink.target_type == "inbound",
                ProcurementChainLink.confirmed.is_(True),
                or_(
                    ProcurementChainLink.match_method.is_(None),
                    ProcurementChainLink.match_method != "rejected",
                ),
            ).all():
                doc_ids.add(l.target_id)
    # 注意：此前 external_po_id 查询被误缩进在 if order 内，导致 PDD/淘宝临时采购单
    # （external_order_id 无 1688 原件）永远收集不到关联入库单、无法反填 SKU 明细。
    # external_po_id 是工作流 PO 主键，与是否存在 1688 原件无关，应无条件执行。
    for l in db.query(ProcurementChainLink).filter(
        ProcurementChainLink.external_po_id == po.id,
        ProcurementChainLink.target_type == "inbound",
        ProcurementChainLink.confirmed.is_(True),
        or_(
            ProcurementChainLink.match_method.is_(None),
            ProcurementChainLink.match_method != "rejected",
        ),
    ).all():
        doc_ids.add(l.target_id)
    # 兼容旧 InboundLink：模型真实字段是 goodsdoc_no，不是 document_id。
    # 优先按入库单号解析；少数历史写入把数字主键放进 raw，也一并兼容。
    legacy_links = db.query(InboundLink).filter_by(po_id=po.id).all()
    legacy_nos = {str(link.goodsdoc_no or "").strip() for link in legacy_links if link.goodsdoc_no}
    docs_by_no = {
        doc.goodsdoc_no: doc.id
        for doc in db.query(JackyunGoodsDocument).filter(
            JackyunGoodsDocument.document_type == "inbound",
            JackyunGoodsDocument.goodsdoc_no.in_(legacy_nos),
        ).all()
    } if legacy_nos else {}
    for link in legacy_links:
        raw = link.raw or {}
        raw_document_id = raw.get("documentId") or raw.get("document_id")
        if raw_document_id is not None:
            try:
                document = db.get(JackyunGoodsDocument, int(raw_document_id))
            except (TypeError, ValueError):
                document = None
            if document is not None and document.document_type == "inbound":
                doc_ids.add(document.id)
                continue
        document_id = docs_by_no.get(str(link.goodsdoc_no or "").strip())
        if document_id is not None:
            doc_ids.add(document_id)
    return doc_ids


def seed_allocations_for_link(
    db: Session,
    link: ProcurementChainLink,
    actor: str = "auto",
) -> dict:
    """以单条链路为入口，反填对应 PO 的 SKU 分配。"""
    po = _po_from_link(db, link)
    if po is None:
        return {"seeded": 0, "skipped": 0, "reason": "未找到对应 PO（仅有文件副本时无法自动反填）"}
    return seed_allocations_for_po(db, po, actor=actor)


def release_inbound_seeds_for_link(
    db: Session,
    link: ProcurementChainLink,
    actor: str = "system",
    *,
    commit: bool = True,
) -> dict:
    """解除/更换入库单链路时，释放该链路来源入库单反填的 SKU 分配行。

    - 仅 PO 处于可编辑状态（pending_refine / draft）时执行，已确认内容不受影响；
    - 只删未被人工编辑过的反填行（source=inbound_auto）；人工修正过的行保留，
      由用户自行决定去留；
    - 每删一行写一条审计（purchase.allocation.release_seed）。
    """
    if link.target_type != "inbound":
        return {"released": 0, "reason": "非入库单链路"}
    doc_id = link.target_id
    po = _po_from_link(db, link)
    if po is None:
        return {"released": 0, "reason": "未找到对应 PO"}
    if po.purchase_status not in SEEDABLE_PO_STATUSES:
        return {"released": 0, "reason": f"PO 状态 {po.purchase_status} 已确认，保留分配行"}

    from app.services.procurement_chain_service import parse_inbound_doc_id
    from app.core.audit import audit
    item_ids = {
        i.id
        for i in db.query(JackyunGoodsDocumentItem.id)
        .filter(JackyunGoodsDocumentItem.document_id == doc_id)
        .all()
    }
    rows = (
        db.query(PurchaseAllocationItem)
        .filter(
            PurchaseAllocationItem.po_id == po.id,
            PurchaseAllocationItem.source == "inbound_auto",
        )
        .all()
    )
    released = 0
    for row in rows:
        if row.source_item_id is not None:
            if row.source_item_id not in item_ids:
                continue
        elif parse_inbound_doc_id(row.note) != doc_id:
            continue
        db.delete(row)
        audit(
            db, actor, "purchase.allocation.release_seed", "purchase_allocation_items", row.id,
            {"poId": po.id, "skuCode": row.sku_code, "docId": doc_id,
             "quantity": str(row.quantity) if row.quantity is not None else None,
             "unitPrice": str(row.unit_price) if row.unit_price is not None else None,
             "amount": str(row.amount) if row.amount is not None else None,
             "sourceItemId": row.source_item_id,
             "note": row.note or "",
             "reason": "解除/更换入库单关联，释放反填行"},
            commit=commit,
        )
        released += 1
    if commit:
        db.commit()
    else:
        db.flush()
    return {"released": released, "poId": po.id, "docId": doc_id}


def seed_allocations_for_po(
    db: Session,
    po: ExternalPurchaseOrder,
    actor: str = "auto",
) -> dict:
    """对给定 PO 反填 SKU 分配：幂等；同一张入库单内同 SKU 只取一行。

    状态守卫：仅 pending_refine / draft 可注入，避免覆盖已确认的人工数据。
    归属排他：入库单明细行一经反填给某个采购单，不能再反填到其他采购单
    （拆分单/共用入库单场景，防止同一行明细重复计入多个订单）。
    拆分收货：同一 PO 的同一 SKU 出现在多张关联入库单时，各建一行分别计量。
    """
    if po.purchase_status not in SEEDABLE_PO_STATUSES:
        return {"seeded": 0, "skipped": 0, "reason": f"PO 状态 {po.purchase_status} 不再自动注入"}
    doc_ids = collect_linked_doc_ids_for_po(db, po)
    if not doc_ids:
        return {"seeded": 0, "skipped": 0, "reason": "无关联入库单"}

    # 本 PO 已有行的三类幂等/优先语义：
    # - manual 行：人工填写优先，该 SKU 不再自动注入；
    # - 历史反填行（无 source_item_id）：保守幂等，该 SKU 不再自动注入；
    # - 新反填行（有 source_item_id）：按明细行幂等；同一张单内同 SKU 只取一行，
    #   跨单（拆分收货）允许再建一行，避免分批入库数量被静默丢掉。
    from app.services.procurement_chain_service import parse_inbound_doc_id

    existing_rows = db.query(PurchaseAllocationItem).filter_by(po_id=po.id).all()
    manual_sku_ids: set[int] = set()
    legacy_sku_ids: set[int] = set()
    own_source_item_ids: set[int] = set()
    own_doc_sku: set[tuple[int, int]] = set()
    for row in existing_rows:
        if row.sku_id is None:
            continue
        if row.source != "inbound_auto":
            manual_sku_ids.add(row.sku_id)
        elif row.source_item_id is None:
            legacy_sku_ids.add(row.sku_id)
        else:
            own_source_item_ids.add(row.source_item_id)
            own_doc_sku.add((parse_inbound_doc_id(row.note), row.sku_id))

    # 其他采购单已占用的明细行 / 旧数据 (doc, sku) 组合。
    # 不按 source 过滤：人工编辑只改 source 字段，行本身仍代表对该明细行的占用；
    # 历史行没有 source_item_id，退化为 (doc_id, sku_id) 排他（保守：已用过即不可再用）。
    occupied_item_ids: set[int] = set()
    occupied_doc_sku: set[tuple[int, int]] = set()
    other_rows = (
        db.query(PurchaseAllocationItem)
        .filter(PurchaseAllocationItem.po_id != po.id)
        .all()
    )
    for other in other_rows:
        other_doc_id = parse_inbound_doc_id(other.note)
        if other_doc_id not in doc_ids:
            continue
        if other.source_item_id is not None:
            occupied_item_ids.add(other.source_item_id)
        elif other.sku_id is not None:
            occupied_doc_sku.add((other_doc_id, other.sku_id))

    items = (
        db.query(JackyunGoodsDocumentItem)
        .filter(
            JackyunGoodsDocumentItem.document_id.in_(doc_ids),
            JackyunGoodsDocumentItem.matched_sku_id.isnot(None),
        )
        .order_by(
            JackyunGoodsDocumentItem.document_id,
            JackyunGoodsDocumentItem.line_no,
        )
        .all()
    )
    # 入库文件可能把同一 SKU 的多笔采购拆成同一张单的多行。
    # 有明确的原始 1688 订单标记时，只能取当前 PO 对应的行；否则旧数据
    # 会按表格顺序把别的采购单的同 SKU 明细错分过来。没有任何标记的老
    # 单据继续沿用下面的保守排他逻辑。
    items_by_doc: dict[int, list[JackyunGoodsDocumentItem]] = defaultdict(list)
    for item in items:
        items_by_doc[item.document_id].append(item)
    is_1688 = (po.platform or "1688").lower() == "1688"
    order_marker = str(po.external_order_id or "").strip() if is_1688 else ""
    candidate_items: list[JackyunGoodsDocumentItem] = []
    for doc_id in sorted(doc_ids):
        doc_items = items_by_doc.get(doc_id, [])
        if not order_marker:
            candidate_items.extend(doc_items)
            continue
        marked_items = [
            item for item in doc_items
            if str((item.raw or {}).get("_1688采购订单") or "").strip()
        ]
        matching_items = [
            item for item in marked_items
            if str((item.raw or {}).get("_1688采购订单") or "").strip() == order_marker
        ]
        if marked_items:
            # 这张入库单已经带有明确订单归属；没有命中当前订单时不猜。
            candidate_items.extend(matching_items)
        else:
            candidate_items.extend(doc_items)
    items = candidate_items

    seeded = 0
    skipped = 0
    skipped_taken = 0
    for it in items:
        sku_id = int(it.matched_sku_id)
        if sku_id in manual_sku_ids or sku_id in legacy_sku_ids:
            skipped += 1
            continue
        if it.id in own_source_item_ids:
            skipped += 1
            continue
        if (it.document_id, sku_id) in own_doc_sku:
            # 同一张入库单内同 SKU 只反填一行（同单多行分属不同订单的场景）
            skipped += 1
            continue
        # 归属排他：该明细行（或旧口径下该 doc+SKU 组合）已被其他采购单占用
        if it.id in occupied_item_ids or (it.document_id, sku_id) in occupied_doc_sku:
            skipped += 1
            skipped_taken += 1
            continue
        sku = db.get(ProductSku, sku_id)
        if sku is None:
            skipped += 1
            continue
        qty = it.quantity or Decimal("0")
        price = it.unit_price_tax
        if price is None and it.amount_tax is not None and qty > 0:
            price = it.amount_tax / qty
        price = price or Decimal("0")
        if qty <= 0:
            skipped += 1
            continue
        row = PurchaseAllocationItem(
            po_id=po.id,
            sku_id=sku.id,
            sku_code=sku.sku_code,
            goods_name=sku.sku_name,
            quantity=qty,
            unit_price=price,
            # 文件导入的“采购总金额”是金额事实。优先保留它，避免四位单价
            # 反算后产生尾差；没有金额时才按数量 × 单价计算。
            amount=(it.amount_tax if it.amount_tax is not None else qty * price).quantize(Decimal("0.0001")),
            source="inbound_auto",
            match_confidence=None,
            note=f"由入库单 #{it.document_id} 明细自动反填",
            source_item_id=it.id,
        )
        db.add(row)
        if sku.cost_mode == "dynamic" and price > 0:
            # 动态成本把实际入库含税单价沉淀到利润成本快照，账期取入库日期。
            from app.services.profit import upsert_cost
            document = db.get(JackyunGoodsDocument, it.document_id)
            period_at = (document.document_at if document and document.document_at else datetime.now(timezone.utc))
            upsert_cost(
                db, sku_id=sku.id, period_year=period_at.year, period_month=period_at.month,
                actual_cost=str(price), source="actual", actor=actor,
            )
        own_doc_sku.add((it.document_id, sku_id))
        seeded += 1

    if seeded > 0:
        db.commit()
    # 入库明细已为每行识别出真实 SKU 且金额平衡时，采购内容直接完成；
    # 只有价格/数量/金额不平或存在未匹配行才留给人工处理。
    allocation_rows = db.query(PurchaseAllocationItem).filter_by(po_id=po.id).all()
    allocation_total = sum(
        (Decimal(str(row.amount or 0)) for row in allocation_rows),
        Decimal("0"),
    )
    payment_gap = _payment_gap(po, allocation_total)
    if payment_gap is not None:
        _ensure_payment_gap_exception(db, po, allocation_total, payment_gap)
    from app.services.cost_policy import allocation_cost_anomalies
    cost_anomalies = allocation_cost_anomalies(db, po) if allocation_rows else []
    if cost_anomalies:
        # 价格异常只进入异常中心，不自动推进采购内容状态。
        from app.models.ops import ExceptionRecord
        existing_exception = db.query(ExceptionRecord).filter(
            ExceptionRecord.code == "PURCHASE_COST_MISMATCH",
            ExceptionRecord.status.in_(("pending", "confirmed")),
        ).first()
        if existing_exception:
            existing_exception.detail = {"latest": "；".join(cost_anomalies[:10])}
        else:
            db.add(ExceptionRecord(code="PURCHASE_COST_MISMATCH", type="PURCHASE_COST_MISMATCH",
                                   title="固定成本与采购单价不一致", detail={"latest": "；".join(cost_anomalies[:10])},
                                   severity="high", source="system"))
        db.commit()
    if (
        po.purchase_status in SEEDABLE_PO_STATUSES
        and allocation_rows
        and all(row.sku_id for row in allocation_rows)
        and not cost_anomalies
        and balance_check(
            po.effective_paid_amount,
            [row.amount for row in allocation_rows],
            [],
        )["allow_mark_refined"]
    ):
        totals = balance_check(po.effective_paid_amount, [row.amount for row in allocation_rows], [])
        po.purchase_status = "confirmed"
        po.refined_at = datetime.now(timezone.utc)
        po.allocated_goods_amount = totals["goods_allocated"]
        po.allocated_expense_amount = totals["expense_allocated"]
        db.commit()
    return {
        "seeded": seeded,
        "skipped": skipped,
        "skippedTaken": skipped_taken,
        "paymentGap": str(payment_gap) if payment_gap is not None else None,
        "doc_ids": sorted(doc_ids),
    }


def seed_allocations_for_order_numbers(db: Session, order_numbers: Iterable[str]) -> dict:
    """补齐历史入库关联对应的工作流 SKU 分配，幂等且遵守状态守卫。"""
    numbers = {str(value).strip() for value in order_numbers if str(value).strip()}
    result = {"orders": 0, "seeded": 0, "skipped": 0}
    if not numbers:
        return result
    for po in db.query(ExternalPurchaseOrder).filter(ExternalPurchaseOrder.external_order_id.in_(numbers)).all():
        result["orders"] += 1
        outcome = seed_allocations_for_po(db, po)
        result["seeded"] += int(outcome.get("seeded", 0))
        result["skipped"] += int(outcome.get("skipped", 0))
    return result


def seed_for_link_batch(db: Session, links: Iterable[ProcurementChainLink]) -> dict:
    """对一组链路逐条调用 seed_allocations_for_link，合并统计。"""
    total_seeded = 0
    total_skipped = 0
    touched_pos: set[int] = set()
    for link in links:
        result = seed_allocations_for_link(db, link)
        total_seeded += int(result.get("seeded", 0))
        total_skipped += int(result.get("skipped", 0))
        if result.get("seeded"):
            po = _po_from_link(db, link)
            if po:
                touched_pos.add(po.id)
    return {
        "seeded": total_seeded,
        "skipped": total_skipped,
        "touched_po_ids": sorted(touched_pos),
    }


def recalc_document_amount(db: Session, document_id: int, actor: str = "system") -> dict:
    """重算入库单金额：head total_amount = Σ 明细 amount_tax；供人工更正后对齐。"""
    from decimal import Decimal as _D
    items = (
        db.query(JackyunGoodsDocumentItem)
        .filter(JackyunGoodsDocumentItem.document_id == document_id)
        .all()
    )
    doc = db.get(JackyunGoodsDocument, document_id)
    if doc is None:
        return {"ok": False, "reason": "入库单不存在"}
    total = sum(
        (_D(str(i.amount_tax)) if i.amount_tax is not None else _D(0))
        for i in items
    )
    before = doc.total_amount
    doc.total_amount = total
    db.commit()
    return {
        "ok": True,
        "documentId": document_id,
        "before": str(before) if before is not None else None,
        "after": str(total),
        "items": len(items),
    }


def set_document_amount(db: Session, document_id: int, amount, actor: str = "system") -> dict:
    """人工直接修改入库单金额（吉客云端录错 head 时的更正入口）。"""
    from decimal import Decimal as _D

    doc = db.get(JackyunGoodsDocument, document_id)
    if doc is None:
        return {"ok": False, "reason": "入库单不存在"}
    value = _D(str(amount))
    if value < 0:
        return {"ok": False, "reason": "金额不能为负"}
    doc.total_amount = value.quantize(_D("0.0001"))
    db.commit()
    return {"ok": True, "documentId": document_id, "after": str(doc.total_amount)}


def sync_allocation_to_inbound(db: Session, po: ExternalPurchaseOrder,
                               allocation: PurchaseAllocationItem,
                               actor: str = "system", was_inbound_auto: bool = False) -> dict:
    """SKU 分配行人工修正后回写入库单明细（源头更正，避免再次反填时用旧单价）。

    映射：PO 已确认关联的入库单 → matched_sku_id 与分配行一致 → 更新该明细的
    quantity/unit_price_tax/amount_tax，随后重算入库单 head（=Σ 明细金额）。
    入库单若被拆到多个 PO（多订单共享同一张单的明细）则跳过，防止互相覆盖。

    仅当分配行原本来自入库单反填（was_inbound_auto=True，调用方在 source 被改写前
    记下）且 PO 可编辑时执行；人工新加行/纯手工订单不回写外部单据。
    """
    if not was_inbound_auto and allocation.source != "inbound_auto":
        return {"synced": 0, "reason": "非入库单反填行，不回写"}
    if allocation.sku_id is None:
        return {"synced": 0, "reason": "分配行无 SKU"}
    if po.purchase_status not in SEEDABLE_PO_STATUSES:
        return {"synced": 0, "reason": f"PO 状态 {po.purchase_status} 不可回写"}
    from decimal import Decimal as _D

    doc_ids = collect_linked_doc_ids_for_po(db, po)
    if not doc_ids:
        return {"synced": 0, "reason": "无关联入库单"}
    # 拆单守卫：doc_ids 中任一 doc 若同时被其他 PO 关联，视为拆分，跳过
    # （简化：仅处理 doc 只关联到当前 PO 的情况）
    shared = False
    for doc_id in doc_ids:
        others = (
            db.query(ProcurementChainLink.id)
            .filter(
                ProcurementChainLink.target_type == "inbound",
                ProcurementChainLink.target_id == doc_id,
                ProcurementChainLink.confirmed.is_(True),
                ProcurementChainLink.external_po_id != po.id,
            )
            .first()
        )
        if others:
            shared = True
            break
    if shared:
        return {"synced": 0, "reason": "入库单被多个订单关联，请在吉客云侧核对后再改"}

    updated = []
    for doc_id in doc_ids:
        if allocation.source_item_id is not None:
            # 新口径：按来源明细行精确回写，避免同单同 SKU 多行时改错行
            item = db.get(JackyunGoodsDocumentItem, allocation.source_item_id)
            if item is None or item.document_id != doc_id:
                continue
            old_sku_id = None
            if was_inbound_auto and item.matched_sku_id != allocation.sku_id:
                # 换货品：允许连同入库明细行的 SKU 匹配一起修正（吉客云行录错货品的场景）
                old_sku_id = item.matched_sku_id
                item.matched_sku_id = allocation.sku_id
                item.match_status = "manual_adjust"
                audit(db, actor, "jackyun.inbound.item.rematch", "jackyun_goods_document_items", item.id, {
                    "documentId": doc_id,
                    "reason": "工作台分摊换货品，自动联动修正入库明细匹配",
                    "old": {"matchedSkuId": old_sku_id},
                    "new": {"matchedSkuId": allocation.sku_id, "skuCode": allocation.sku_code},
                })
        else:
            item = (
                db.query(JackyunGoodsDocumentItem)
                .filter(
                    JackyunGoodsDocumentItem.document_id == doc_id,
                    JackyunGoodsDocumentItem.matched_sku_id == allocation.sku_id,
                )
                .first()
            )
            if item is None:
                continue
        qty = allocation.quantity
        price = allocation.unit_price
        if qty is None or price is None or qty <= 0:
            continue
        old = {"quantity": str(item.quantity), "unitPriceTax": str(item.unit_price_tax),
               "amountTax": str(item.amount_tax)}
        item.quantity = qty
        item.unit_price_tax = price
        item.amount_tax = (qty * price).quantize(_D("0.0001"))
        if item.match_status in ("price_mismatch", "auto"):
            item.match_status = "manual_adjust"
        if old_sku_id is not None:
            item.match_note = f"按工作台 SKU 分配人工修正货品为 {allocation.sku_code}（原匹配 SKU id {old_sku_id}）并调整 数量/单价（原 {old}）"
        else:
            item.match_note = f"按工作台 SKU 分配人工修正 {allocation.sku_code} 数量/单价（原 {old}）"
        updated.append({"documentId": doc_id, "itemId": item.id, "old": old})
    if updated:
        db.commit()
    # 重算每张 doc 的 head
    for doc_id in doc_ids:
        recalc_document_amount(db, doc_id, actor=actor)
    return {"synced": len(updated), "items": updated}
