"""采购完成闭环校验与共用入库拆分。

目标：
- 采购关联支持合并/拆分标注（relation_kind），分摊金额必须闭环；
- 共用入库明细允许显式按数量拆给多个来源单，但总量不得超过吉客云实际入库；
- “完成”不再只看有没有单据，而是校验入库 / 发票 / 付款 / 认证覆盖率。

本模块不删除、不重写任何外部原始单据，只维护关联与校验结果。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.models.alibaba1688_import import Alibaba1688Order
from app.models.jackyun import (
    JackyunGoodsDocument,
    JackyunGoodsDocumentItem,
    JackyunPurchaseSettlement,
)
from app.models.procurement_chain import ProcurementChainLink
from app.models.purchase import (
    ExternalPurchaseOrder,
    JackyunPurchaseOrder,
    JackyunPurchaseOrderLink,
    PurchaseAllocationItem,
    PurchaseInvoice,
    PurchaseInvoiceLink,
)
from app.models.tax import TaxInvoice, TaxInvoiceLink
from app.utils.money import to_decimal

MONEY_EPS = Decimal("0.05")
QTY_EPS = Decimal("0.0001")


def _d(value: Any) -> Decimal:
    parsed = to_decimal(value)
    if parsed is None or not parsed.is_finite():
        return Decimal("0")
    return parsed


def _tol(target: Decimal) -> Decimal:
    return max(abs(target) * Decimal("0.02"), MONEY_EPS)


def _close(a: Decimal, b: Decimal, tolerance: Decimal | None = None) -> bool:
    return abs(a - b) <= (tolerance if tolerance is not None else _tol(b))


def _source_order(db: Session, po: ExternalPurchaseOrder) -> Alibaba1688Order | None:
    if (po.platform or "").lower() != "1688":
        return None
    return db.query(Alibaba1688Order).filter_by(external_order_id=po.external_order_id).first()


def _po_for_source_order(db: Session, order_id: int | None) -> ExternalPurchaseOrder | None:
    if not order_id:
        return None
    source = db.get(Alibaba1688Order, order_id)
    if source is None:
        return None
    return db.query(ExternalPurchaseOrder).filter_by(
        platform="1688", external_order_id=source.external_order_id
    ).first()


def _po_for_chain_link(db: Session, link: ProcurementChainLink) -> ExternalPurchaseOrder | None:
    if link.external_po_id:
        return db.get(ExternalPurchaseOrder, link.external_po_id)
    return _po_for_source_order(db, link.order_id)


def _chain_links_for_po(db: Session, po: ExternalPurchaseOrder, target_type: str) -> list[ProcurementChainLink]:
    source = _source_order(db, po)
    query = db.query(ProcurementChainLink).filter(
        ProcurementChainLink.target_type == target_type,
        ProcurementChainLink.confirmed.is_(True),
        or_(
            ProcurementChainLink.match_method.is_(None),
            ProcurementChainLink.match_method != "rejected",
        ),
    )
    if source is not None:
        query = query.filter(or_(
            ProcurementChainLink.order_id == source.id,
            ProcurementChainLink.external_po_id == po.id,
        ))
    else:
        query = query.filter(ProcurementChainLink.external_po_id == po.id)
    return query.all()


def _document_amount(db: Session, document: JackyunGoodsDocument) -> Decimal:
    if document.total_amount is not None:
        return _d(document.total_amount)
    total = Decimal("0")
    for item in db.query(JackyunGoodsDocumentItem).filter_by(document_id=document.id).all():
        if item.amount_tax is not None:
            total += _d(item.amount_tax)
        elif item.quantity is not None and item.unit_price_tax is not None:
            total += _d(item.quantity) * _d(item.unit_price_tax)
    return total


def _settlement_paid_amount(row: JackyunPurchaseSettlement) -> Decimal:
    if row.paid is not None:
        return max(_d(row.paid), Decimal("0"))
    status = (row.status or "").lower()
    if any(token in status for token in ("paid", "已付", "完成", "结清")):
        return max(_d(row.settlement_amount or row.total_amount), Decimal("0"))
    return Decimal("0")


def _shared_target_ratio(db: Session, po: ExternalPurchaseOrder, target_type: str, target_id: int) -> Decimal | None:
    """同一入库/结算单被多个来源单共用时，按共同吉客云采购单的显式分摊金额计算占比。"""
    all_links = db.query(ProcurementChainLink).filter(
        ProcurementChainLink.target_type == target_type,
        ProcurementChainLink.target_id == target_id,
        ProcurementChainLink.confirmed.is_(True),
        or_(
            ProcurementChainLink.match_method.is_(None),
            ProcurementChainLink.match_method != "rejected",
        ),
    ).all()
    peer_pos = [candidate for candidate in (_po_for_chain_link(db, link) for link in all_links) if candidate is not None]
    peer_ids = {candidate.id for candidate in peer_pos}
    if len(peer_ids) <= 1:
        return Decimal("1")
    if po.id not in peer_ids:
        return None

    current_links = db.query(JackyunPurchaseOrderLink).filter(
        JackyunPurchaseOrderLink.po_id == po.id,
        JackyunPurchaseOrderLink.relation_kind == "merged",
    ).all()
    for current in current_links:
        group_links = db.query(JackyunPurchaseOrderLink).filter_by(jackyun_po_id=current.jackyun_po_id).all()
        group_po_ids = {row.po_id for row in group_links}
        if not peer_ids.issubset(group_po_ids):
            continue
        amounts = {row.po_id: _d(row.alloc_amount) for row in group_links}
        denominator = sum((amounts.get(pid, Decimal("0")) for pid in peer_ids), Decimal("0"))
        numerator = amounts.get(po.id, Decimal("0"))
        if denominator > 0 and numerator > 0:
            return numerator / denominator
    return None


def jackyun_group_summary(db: Session, jackyun_po_id: int) -> dict[str, Any]:
    jpo = db.get(JackyunPurchaseOrder, jackyun_po_id)
    if jpo is None:
        raise ValueError("吉客云采购单不存在")
    links = db.query(JackyunPurchaseOrderLink).filter_by(jackyun_po_id=jpo.id).order_by(
        JackyunPurchaseOrderLink.id
    ).all()
    rows = []
    allocated = Decimal("0")
    missing = 0
    for link in links:
        po = db.get(ExternalPurchaseOrder, link.po_id)
        alloc = _d(link.alloc_amount)
        if link.relation_kind == "merged" and alloc <= 0:
            missing += 1
        allocated += alloc
        rows.append({
            "linkId": link.id,
            "poId": link.po_id,
            "platform": po.platform if po else "",
            "orderNo": po.external_order_id if po else "",
            "supplier": po.supplier_name if po else "",
            "orderTarget": str(po.effective_paid_amount or po.order_amount or Decimal("0")) if po else "0",
            "allocAmount": str(link.alloc_amount) if link.alloc_amount is not None else None,
            "relationKind": link.relation_kind or "",
        })
    amount = _d(jpo.amount)
    difference = amount - allocated
    balanced = (
        len(links) >= 2
        and missing == 0
        and amount > 0
        and abs(difference) <= MONEY_EPS
        and all((link.relation_kind or "") == "merged" for link in links)
    )
    return {
        "jackyunPoId": jpo.id,
        "purchNo": jpo.purch_no or jpo.jackyun_purch_id,
        "supplier": jpo.supplier_name or "",
        "amount": str(amount),
        "allocated": str(allocated),
        "difference": str(difference),
        "balanced": balanced,
        "missingAllocationCount": missing,
        "orderCount": len(links),
        "orders": rows,
    }


def assign_shared_inbound_item(
    db: Session,
    *,
    source_item_id: int,
    assignments: list[dict[str, Any]],
    actor: str = "system",
) -> dict[str, Any]:
    """把一条吉客云入库明细显式分给同一合并组内的多个采购内容行。

    这里只给既有 PurchaseAllocationItem 标记真实入库来源，不新增金额，避免采购金额翻倍。
    为保护采购内容，分摊数量必须与既有采购内容行数量一致；需要改采购数量时先“重新编辑”。
    """
    item = db.get(JackyunGoodsDocumentItem, source_item_id)
    if item is None:
        raise ValueError("吉客云入库明细不存在")
    if item.matched_sku_id is None:
        raise ValueError("这条入库明细尚未匹配 SKU")
    if len(assignments) < 2:
        raise ValueError("共用入库明细至少需要分给 2 张采购订单")

    rows: list[PurchaseAllocationItem] = []
    po_ids: set[int] = set()
    proposed_qty = Decimal("0")
    for assignment in assignments:
        allocation_id = int(assignment.get("allocation_id") or assignment.get("allocationId") or 0)
        po_id = int(assignment.get("po_id") or assignment.get("poId") or 0)
        qty = _d(assignment.get("quantity"))
        row = db.get(PurchaseAllocationItem, allocation_id)
        if row is None or row.po_id != po_id:
            raise ValueError("入库分摊行与采购订单不匹配")
        if row.sku_id != item.matched_sku_id:
            raise ValueError(f"采购分配行 {allocation_id} 的 SKU 与入库明细不一致")
        if qty <= 0 or abs(qty - _d(row.quantity)) > QTY_EPS:
            raise ValueError("入库分摊数量必须等于当前采购内容行数量；如需修改数量请先重新编辑采购内容")
        if row.source_item_id not in (None, source_item_id):
            raise ValueError(f"采购分配行 {allocation_id} 已绑定其他入库明细")
        rows.append(row)
        po_ids.add(po_id)
        proposed_qty += qty

    if len(po_ids) < 2:
        raise ValueError("请选择至少 2 张不同的采购订单")
    common_jpo_ids: set[int] | None = None
    for po_id in po_ids:
        merged_ids = {
            link.jackyun_po_id
            for link in db.query(JackyunPurchaseOrderLink).filter_by(
                po_id=po_id, relation_kind="merged"
            ).all()
        }
        common_jpo_ids = merged_ids if common_jpo_ids is None else common_jpo_ids & merged_ids
    if not common_jpo_ids:
        raise ValueError("这些采购订单不属于同一个吉客云合并采购组")

    existing_qty = sum((
        _d(row.quantity)
        for row in db.query(PurchaseAllocationItem).filter(
            PurchaseAllocationItem.source_item_id == source_item_id,
            PurchaseAllocationItem.id.notin_([candidate.id for candidate in rows]),
        ).all()
    ), Decimal("0"))
    actual_qty = _d(item.quantity)
    if actual_qty <= 0:
        raise ValueError("吉客云入库明细缺少有效入库数量")
    if existing_qty + proposed_qty > actual_qty + QTY_EPS:
        raise ValueError(
            f"入库分摊数量合计 {existing_qty + proposed_qty} 超过吉客云实际入库数量 {actual_qty}"
        )

    for row in rows:
        row.source_item_id = source_item_id
        row.source = "inbound_split"
        marker = f"合并采购入库分摊：吉客云入库明细 #{source_item_id}"
        if marker not in (row.note or ""):
            row.note = f"{row.note}；{marker}" if row.note else marker
    db.commit()
    audit(
        db,
        actor,
        "purchase.inbound.shared_split",
        "jackyun_goods_document_items",
        source_item_id,
        {
            "poIds": sorted(po_ids),
            "allocationIds": [row.id for row in rows],
            "allocatedQuantity": str(existing_qty + proposed_qty),
            "actualQuantity": str(actual_qty),
        },
    )
    return {
        "ok": True,
        "sourceItemId": source_item_id,
        "allocatedQuantity": str(existing_qty + proposed_qty),
        "actualQuantity": str(actual_qty),
        "remainingQuantity": str(actual_qty - existing_qty - proposed_qty),
        "poIds": sorted(po_ids),
    }


def _jpo_side_closed(db: Session, po: ExternalPurchaseOrder, target: Decimal) -> tuple[bool, Decimal, list[str]]:
    links = db.query(JackyunPurchaseOrderLink).filter_by(po_id=po.id).all()
    if not links:
        bypassed = bool((po.raw or {}).get("jackyunPoBypassed"))
        return bypassed, Decimal("0"), [] if bypassed else ["未关联吉客云采购单"]
    issues: list[str] = []
    allocated = Decimal("0")
    complex_relation = any((link.relation_kind or "") in ("merged", "split") for link in links)
    for link in links:
        jpo = db.get(JackyunPurchaseOrder, link.jackyun_po_id)
        if (link.relation_kind or "") in ("merged", "split"):
            if link.alloc_amount is None or _d(link.alloc_amount) <= 0:
                issues.append("合并/拆分采购单存在未填写分摊金额")
            allocated += _d(link.alloc_amount)
            if (link.relation_kind or "") == "merged":
                group = jackyun_group_summary(db, link.jackyun_po_id)
                if not group["balanced"]:
                    issues.append(f"吉客云采购单 {group['purchNo']} 的合并分摊未闭环")
        elif jpo is not None:
            allocated += _d(link.alloc_amount) if link.alloc_amount is not None else min(_d(jpo.amount), target)
    if target > 0 and not _close(allocated, target):
        issues.append(f"吉客云采购分摊覆盖 {allocated}/{target}")
    if complex_relation and any(_d(link.alloc_amount) <= 0 for link in links):
        return False, allocated, issues
    return not issues, allocated, issues


def _invoice_coverage(db: Session, po: ExternalPurchaseOrder) -> tuple[Decimal, Decimal, int]:
    source = _source_order(db, po)
    predicates = [("external_purchase_order", po.id)]
    if source is not None:
        predicates.append(("alibaba1688_order", source.id))

    direct_invoice_ids: set[int] = set()
    invoice_amount = Decimal("0")
    verified_amount = Decimal("0")
    for target_type, target_id in predicates:
        for link in db.query(TaxInvoiceLink).filter_by(
            target_type=target_type, target_id=target_id, confirmed=True
        ).all():
            if link.match_method == "rejected" or link.invoice_id in direct_invoice_ids:
                continue
            inv = db.get(TaxInvoice, link.invoice_id)
            if inv is None or (inv.status or "").lower() in ("red", "void") or _d(inv.total_amount) <= 0:
                continue
            amount = _d(link.allocated_amount) if link.allocated_amount is not None else _d(inv.total_amount)
            direct_invoice_ids.add(inv.id)
            invoice_amount += amount
            if inv.verified:
                verified_amount += amount

    # 税务清单若明确写的是吉客云采购单号，会落在 jackyun_purchase_order；
    # 对合并组按采购单分摊金额比例映射回来源订单，避免采购页显示“待发票”。
    for po_link in db.query(JackyunPurchaseOrderLink).filter_by(po_id=po.id).all():
        jpo = db.get(JackyunPurchaseOrder, po_link.jackyun_po_id)
        if jpo is None:
            continue
        group_links = db.query(JackyunPurchaseOrderLink).filter_by(jackyun_po_id=jpo.id).all()
        group_total = sum((_d(row.alloc_amount) for row in group_links), Decimal("0"))
        if len(group_links) == 1 and po_link.alloc_amount is None:
            share = Decimal("1")
        elif group_total > 0 and _d(po_link.alloc_amount) > 0:
            share = _d(po_link.alloc_amount) / group_total
        else:
            continue
        for link in db.query(TaxInvoiceLink).filter_by(
            target_type="jackyun_purchase_order", target_id=jpo.id, confirmed=True
        ).all():
            if link.match_method == "rejected" or link.invoice_id in direct_invoice_ids:
                continue
            inv = db.get(TaxInvoice, link.invoice_id)
            if inv is None or (inv.status or "").lower() in ("red", "void") or _d(inv.total_amount) <= 0:
                continue
            base = _d(link.allocated_amount) if link.allocated_amount is not None else _d(inv.total_amount)
            amount = base * share
            direct_invoice_ids.add(inv.id)
            invoice_amount += amount
            if inv.verified:
                verified_amount += amount

    # 兼容旧手工发票登记：计入开票覆盖，但不视为税务认证凭证。
    for link in db.query(PurchaseInvoiceLink).filter_by(po_id=po.id).all():
        inv = db.get(PurchaseInvoice, link.invoice_id)
        if inv is not None:
            invoice_amount += _d(link.allocated_amount if link.allocated_amount is not None else inv.invoice_amount)
    return invoice_amount, verified_amount, len(direct_invoice_ids)


def _inbound_coverage(db: Session, po: ExternalPurchaseOrder) -> tuple[Decimal, bool, int, list[str]]:
    links = _chain_links_for_po(db, po, "inbound")
    covered = Decimal("0")
    issues: list[str] = []
    for link in links:
        document = db.get(JackyunGoodsDocument, link.target_id)
        if document is None:
            continue
        amount = _document_amount(db, document)
        ratio = _shared_target_ratio(db, po, "inbound", link.target_id)
        if ratio is None:
            issues.append(f"共用入库单 {document.goodsdoc_no} 尚未按合并采购金额分摊")
            continue
        covered += amount * ratio

    backed = sum((
        _d(row.amount)
        for row in db.query(PurchaseAllocationItem).filter(
            PurchaseAllocationItem.po_id == po.id,
            PurchaseAllocationItem.source_item_id.isnot(None),
        ).all()
    ), Decimal("0"))
    covered = max(covered, backed)

    complex_relation = db.query(JackyunPurchaseOrderLink).filter(
        JackyunPurchaseOrderLink.po_id == po.id,
        JackyunPurchaseOrderLink.relation_kind.in_(("merged", "split")),
    ).first() is not None
    quantity_closed = True
    if complex_relation:
        planned = db.query(PurchaseAllocationItem).filter(
            PurchaseAllocationItem.po_id == po.id,
            PurchaseAllocationItem.quantity > 0,
        ).all()
        unbound = [row for row in planned if row.source_item_id is None]
        if unbound:
            quantity_closed = False
            issues.append(f"合并采购仍有 {len(unbound)} 条 SKU 未明确分到真实入库明细")
        source_ids = {row.source_item_id for row in planned if row.source_item_id is not None}
        for source_item_id in source_ids:
            item = db.get(JackyunGoodsDocumentItem, source_item_id)
            if item is None:
                quantity_closed = False
                issues.append(f"入库明细 #{source_item_id} 已不存在")
                continue
            allocated_qty = sum((
                _d(row.quantity)
                for row in db.query(PurchaseAllocationItem).filter_by(source_item_id=source_item_id).all()
            ), Decimal("0"))
            if allocated_qty > _d(item.quantity) + QTY_EPS:
                quantity_closed = False
                issues.append(
                    f"入库明细 #{source_item_id} 分摊 {allocated_qty} 超过实际入库 {_d(item.quantity)}"
                )
    return covered, quantity_closed, len(links), issues


def _payment_coverage(db: Session, po: ExternalPurchaseOrder) -> tuple[Decimal, int, list[str]]:
    links = _chain_links_for_po(db, po, "settlement")
    covered = Decimal("0")
    issues: list[str] = []
    for link in links:
        settlement = db.get(JackyunPurchaseSettlement, link.target_id)
        if settlement is None:
            continue
        amount = _settlement_paid_amount(settlement)
        ratio = _shared_target_ratio(db, po, "settlement", link.target_id)
        if ratio is None:
            issues.append(f"共用结算单 {settlement.settlement_no} 尚未按合并采购金额分摊")
            continue
        covered += amount * ratio
    return covered, len(links), issues


def completion_snapshot(db: Session, po: ExternalPurchaseOrder) -> dict[str, Any]:
    target = _d(po.effective_paid_amount if po.effective_paid_amount is not None else po.order_amount)
    issues: list[str] = []
    if target <= 0:
        issues.append("采购订单缺少有效实付/采购金额")

    jpo_closed, jpo_amount, jpo_issues = _jpo_side_closed(db, po, target)
    issues.extend(jpo_issues)

    inbound_amount, quantity_closed, inbound_count, inbound_issues = _inbound_coverage(db, po)
    issues.extend(inbound_issues)
    inbound_closed = target > 0 and quantity_closed and _close(inbound_amount, target)
    if inbound_count == 0:
        issues.append("未关联真实采购入库单")
    elif not inbound_closed:
        issues.append(f"入库覆盖 {inbound_amount}/{target}")

    invoice_amount, verified_amount, invoice_count = _invoice_coverage(db, po)
    invoice_closed = target > 0 and _close(invoice_amount, target)
    verified_closed = target > 0 and _close(verified_amount, target)
    if invoice_count == 0 and invoice_amount <= 0:
        issues.append("未关联有效进项发票")
    elif not invoice_closed:
        issues.append(f"发票覆盖 {invoice_amount}/{target}")
    if not verified_closed:
        issues.append(f"已认证发票覆盖 {verified_amount}/{target}")

    payment_amount, settlement_count, payment_issues = _payment_coverage(db, po)
    issues.extend(payment_issues)
    payment_closed = target > 0 and _close(payment_amount, target)
    if settlement_count == 0:
        issues.append("未关联采购付款/结算单")
    elif not payment_closed:
        issues.append(f"付款覆盖 {payment_amount}/{target}")

    complete = bool(
        target > 0
        and jpo_closed
        and inbound_closed
        and invoice_closed
        and payment_closed
        and verified_closed
    )
    return {
        "poId": po.id,
        "platform": po.platform or "other",
        "orderNo": po.external_order_id,
        "targetAmount": str(target),
        "jackyunAllocated": str(jpo_amount),
        "jackyunClosed": jpo_closed,
        "inboundAmount": str(inbound_amount),
        "inboundCount": inbound_count,
        "inboundQuantityClosed": quantity_closed,
        "inboundClosed": inbound_closed,
        "invoiceAmount": str(invoice_amount),
        "invoiceCount": invoice_count,
        "invoiceClosed": invoice_closed,
        "verifiedAmount": str(verified_amount),
        "verifiedClosed": verified_closed,
        "paymentAmount": str(payment_amount),
        "settlementCount": settlement_count,
        "paymentClosed": payment_closed,
        "complete": complete,
        "issues": list(dict.fromkeys(issues)),
    }


def install_purchase_guards() -> None:
    """兼容旧导入点：采购强校验已直接写入 purchase_service，不再做运行时函数替换。"""
    return None
