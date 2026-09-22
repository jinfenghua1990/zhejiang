"""采购订单发票事实投影。

TaxInvoice / TaxInvoiceLink 是正式税务事实源；PurchaseInvoice / PurchaseInvoiceLink
只保留历史手工登记兼容。所有采购页面应从本模块读取“已收票金额/状态”，不得直接
使用 ExternalPurchaseOrder.invoice_status 或自行累加两套发票表。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.alibaba1688_import import Alibaba1688Order
from app.models.purchase import ExternalPurchaseOrder, PurchaseInvoice, PurchaseInvoiceLink
from app.models.tax import TaxInvoice, TaxInvoiceLink
from app.services import tax_invoice_service
from app.utils.money import to_decimal

EPS = Decimal("0.01")


def _money(value: Any) -> Decimal:
    try:
        result = to_decimal(value)
    except Exception:
        return Decimal("0")
    return result if result.is_finite() else Decimal("0")


def _norm_invoice_no(value: str | None) -> str:
    return "".join(str(value or "").split()).upper()


def _target_amount(order: Alibaba1688Order | None, external: ExternalPurchaseOrder | None) -> Decimal | None:
    if order is not None and order.actual_payment is not None:
        amount = _money(order.actual_payment)
        return amount if amount > 0 else None
    if external is not None:
        if external.effective_paid_amount is not None:
            amount = _money(external.effective_paid_amount)
            if amount > 0:
                return amount
        if external.order_amount is not None:
            amount = _money(external.order_amount)
            return amount if amount > 0 else None
    return None


def _logical_refs(
    db: Session,
    order: Alibaba1688Order | None,
    external: ExternalPurchaseOrder | None,
    prefetch: Any | None = None,
) -> set[tuple[str, int]]:
    refs: set[tuple[str, int]] = set()
    if order is not None:
        refs.add(("alibaba1688_order", order.id))
    if external is not None:
        refs.add(("external_purchase_order", external.id))

    # ChainPrefetch 已经把同一订单对放在一起，列表场景不再为 alias 额外点查。
    if prefetch is not None:
        if order is not None and external is None:
            alias = getattr(prefetch, "external_by_no", {}).get(order.external_order_id)
            if alias is not None and str(alias.platform or "").lower() == "1688":
                refs.add(("external_purchase_order", alias.id))
        if external is not None and order is None and str(external.platform or "").lower() == "1688":
            alias = getattr(prefetch, "alibaba_by_no", {}).get(external.external_order_id)
            if alias is not None:
                refs.add(("alibaba1688_order", alias.id))
        return refs

    if order is not None:
        refs.update(
            tax_invoice_service.purchase_alias_target_refs(
                db, "alibaba1688_order", order.id, target=order
            )
        )
    if external is not None:
        refs.update(
            tax_invoice_service.purchase_alias_target_refs(
                db, "external_purchase_order", external.id, target=external
            )
        )
    return refs


def _link_filter(refs: set[tuple[str, int]]):
    clauses = [
        and_(
            TaxInvoiceLink.target_type == target_type,
            TaxInvoiceLink.target_id == target_id,
        )
        for target_type, target_id in refs
    ]
    return or_(*clauses)


def _invoice_group_allocations(
    db: Session,
    invoice: TaxInvoice,
    prefetch: Any | None = None,
) -> tuple[dict[tuple[Any, ...], Decimal], bool, int]:
    """按逻辑采购单汇总正式发票分摊。

    1688 原始单/工作流副本若历史上同时存在链接，只取该逻辑订单最大一条分摊，
    防止 alias 重复计票，同时把 duplicates 返回给上层暴露为需复核。
    """
    if prefetch is not None:
        links = [
            link
            for link in getattr(prefetch, "invoice_links_by_invoice", {}).get(invoice.id, [])
            if link.target_type in tax_invoice_service.PURCHASE_LINK_TARGET_TYPES
            and link.match_method != "rejected"
            and link.confirmed
        ]
    else:
        links = (
            db.query(TaxInvoiceLink)
            .filter(
                TaxInvoiceLink.invoice_id == invoice.id,
                TaxInvoiceLink.target_type.in_(tax_invoice_service.PURCHASE_LINK_TARGET_TYPES),
                TaxInvoiceLink.match_method != "rejected",
                TaxInvoiceLink.confirmed.is_(True),
            )
            .all()
        )
    groups: dict[tuple[Any, ...], Decimal] = {}
    unknown = False
    duplicate_count = 0
    seen_counts: dict[tuple[tuple[str, int], ...], int] = {}
    for link in links:
        if prefetch is not None:
            if link.target_type == "alibaba1688_order":
                target = getattr(prefetch, "alibaba_orders", {}).get(link.target_id)
                key = ("1688", target.external_order_id) if target is not None else ("alibaba1688_order", link.target_id)
            elif link.target_type == "external_purchase_order":
                target = getattr(prefetch, "external_by_id", {}).get(link.target_id)
                if target is not None and str(target.platform or "").lower() == "1688":
                    key = ("1688", target.external_order_id)
                else:
                    key = ("external_purchase_order", link.target_id)
            else:
                key = ("jackyun_purchase_order", link.target_id)
        else:
            key = tax_invoice_service.purchase_logical_target_key(
                db, link.target_type, link.target_id
            )
        seen_counts[key] = seen_counts.get(key, 0) + 1
        if link.allocated_amount is None:
            unknown = True
            continue
        amount = _money(link.allocated_amount)
        if amount <= 0:
            continue
        previous = groups.get(key)
        groups[key] = amount if previous is None else max(previous, amount)
    duplicate_count = sum(max(count - 1, 0) for count in seen_counts.values())
    return groups, unknown, duplicate_count


def _tax_entries(
    db: Session,
    refs: set[tuple[str, int]],
    prefetch: Any | None = None,
) -> tuple[list[dict], Decimal, list[str], set[str]]:
    if not refs:
        return [], Decimal("0"), [], set()

    if prefetch is not None:
        links = []
        for ref in refs:
            links.extend(getattr(prefetch, "invoice_links", {}).get(ref, []))
        links = sorted(
            (
                link for link in links
                if link.match_method != "rejected" and link.confirmed
            ),
            key=lambda row: row.id,
        )
    else:
        links = (
            db.query(TaxInvoiceLink)
            .filter(
                _link_filter(refs),
                TaxInvoiceLink.match_method != "rejected",
                TaxInvoiceLink.confirmed.is_(True),
            )
            .order_by(TaxInvoiceLink.id)
            .all()
        )
    invoice_ids = sorted({link.invoice_id for link in links})
    if not invoice_ids:
        return [], Decimal("0"), [], set()

    if prefetch is not None:
        invoices = {
            invoice_id: getattr(prefetch, "invoices", {}).get(invoice_id)
            for invoice_id in invoice_ids
            if getattr(prefetch, "invoices", {}).get(invoice_id) is not None
        }
    else:
        visible = tax_invoice_service.filter_visible_invoices(
            db.query(TaxInvoice).filter(TaxInvoice.id.in_(invoice_ids))
        ).all()
        invoices = {row.id: row for row in visible}
    links_by_invoice: dict[int, list[TaxInvoiceLink]] = {}
    for link in links:
        if link.invoice_id in invoices:
            links_by_invoice.setdefault(link.invoice_id, []).append(link)

    if prefetch is not None:
        current_keys: set[tuple[Any, ...]] = set()
        for target_type, target_id in refs:
            if target_type == "alibaba1688_order":
                target = getattr(prefetch, "alibaba_orders", {}).get(target_id)
                current_keys.add(("1688", target.external_order_id) if target is not None else (target_type, target_id))
            elif target_type == "external_purchase_order":
                target = getattr(prefetch, "external_by_id", {}).get(target_id)
                if target is not None and str(target.platform or "").lower() == "1688":
                    current_keys.add(("1688", target.external_order_id))
                else:
                    current_keys.add((target_type, target_id))
            else:
                current_keys.add((target_type, target_id))
    else:
        current_keys = {
            tax_invoice_service.purchase_logical_target_key(db, target_type, target_id)
            for target_type, target_id in refs
        }
    entries: list[dict] = []
    total = Decimal("0")
    review_reasons: list[str] = []
    tax_numbers: set[str] = set()

    for invoice_id, current_links in links_by_invoice.items():
        invoice = invoices[invoice_id]
        number_key = _norm_invoice_no(invoice.invoice_number)
        full_number_key = _norm_invoice_no(
            f"{invoice.invoice_code or ''}{invoice.invoice_number or ''}"
        )
        if number_key:
            tax_numbers.add(number_key)
        if full_number_key:
            tax_numbers.add(full_number_key)

        groups, unknown, duplicate_count = _invoice_group_allocations(db, invoice, prefetch=prefetch)
        current_alloc = max((groups.get(key, Decimal("0")) for key in current_keys), default=Decimal("0"))
        all_alloc = sum(groups.values(), Decimal("0"))
        if prefetch is not None:
            red = getattr(prefetch, "invoice_red_context", {}).get(invoice.id, {})
            if (
                red.get("invoiceColor") == "blue"
                and red.get("redStatus") not in {
                    "fully_red_offset", "blue_red_pending", "over_red_offset", "void", "unknown"
                }
            ):
                effective_total = _money(red.get("remainingAfterRedAmount"))
            else:
                effective_total = Decimal("0")
        else:
            effective_total = _money(tax_invoice_service.effective_invoice_amount_after_red(db, invoice))
        original_total = _money(invoice.total_amount)

        link = current_links[0]
        contribution = Decimal("0")
        issue = ""
        if invoice.direction != "input":
            issue = "采购单误关联了非进项发票"
        elif unknown:
            issue = "存在已确认但缺少分摊金额的历史发票关联"
        elif duplicate_count:
            issue = "同一逻辑采购单存在重复发票链接"
        elif effective_total <= 0:
            issue = "发票已红冲/作废或当前无有效蓝字余额"
        elif current_alloc <= 0:
            issue = "发票关联缺少有效分摊金额"
        elif all_alloc <= effective_total + EPS:
            contribution = current_alloc
        elif len(groups) == 1:
            # 只有一个逻辑采购单时，红冲净额可以无歧义地全部归到该订单。
            contribution = min(current_alloc, effective_total)
        else:
            # 一票多单后又发生红冲，红冲究竟冲哪一张订单无法从现有事实判断。
            issue = "一票多单红冲后原分摊超过有效蓝字净额，需人工重新分摊"

        if issue and issue not in review_reasons:
            review_reasons.append(issue)
        total += contribution
        entries.append({
            "invoiceId": invoice.id,
            "linkId": link.id,
            "invoiceKind": "tax",
            "invoiceNo": invoice.invoice_number,
            "amount": float(contribution),
            "originalAmount": float(original_total),
            "effectiveInvoiceAmount": float(effective_total),
            "allocatedAmount": float(current_alloc) if current_alloc > 0 else None,
            "issueDate": invoice.issue_date.isoformat() if invoice.issue_date else None,
            "verified": bool(invoice.verified),
            "verifiedMonth": invoice.verified_month or "",
            "confirmed": True,
            "matchMethod": link.match_method or "",
            "confidence": float(link.confidence) if link.confidence is not None else None,
            "note": link.note or "",
            "status": invoice.status,
            "redAdjusted": effective_total != max(original_total, Decimal("0")),
            "needsReview": bool(issue),
            "reviewReason": issue,
        })

    return entries, total, review_reasons, tax_numbers


def _legacy_entries(
    db: Session,
    external: ExternalPurchaseOrder | None,
    official_tax_numbers: set[str],
    prefetch: Any | None = None,
) -> tuple[list[dict], Decimal, list[str]]:
    if external is None:
        return [], Decimal("0"), []

    if prefetch is not None:
        rows = sorted(
            getattr(prefetch, "purchase_invoice_links", {}).get(external.id, []),
            key=lambda pair: pair[0].id,
        )
    else:
        rows = (
            db.query(PurchaseInvoiceLink, PurchaseInvoice)
            .join(PurchaseInvoice, PurchaseInvoice.id == PurchaseInvoiceLink.invoice_id)
            .filter(PurchaseInvoiceLink.po_id == external.id)
            .order_by(PurchaseInvoiceLink.id)
            .all()
        )
    entries: list[dict] = []
    total = Decimal("0")
    reasons: list[str] = []
    for link, invoice in rows:
        number_key = _norm_invoice_no(invoice.invoice_no)
        if number_key and number_key in official_tax_numbers:
            # 正式税务台账优先；旧手工记录仅留审计，不再重复计票。
            continue
        amount = _money(link.allocated_amount)
        if amount <= 0:
            reason = "旧手工发票关联缺少有效分摊金额"
            if reason not in reasons:
                reasons.append(reason)
            continue
        total += amount
        entries.append({
            "invoiceId": invoice.id,
            "linkId": link.id,
            "invoiceKind": "manual",
            "invoiceNo": invoice.invoice_no,
            "amount": float(amount),
            "originalAmount": float(_money(invoice.invoice_amount)),
            "effectiveInvoiceAmount": float(amount),
            "allocatedAmount": float(amount),
            "issueDate": invoice.invoice_date.isoformat() if invoice.invoice_date else None,
            "verified": False,
            "verifiedMonth": "",
            "confirmed": True,
            "matchMethod": "legacy_manual",
            "confidence": 1.0,
            "note": "历史采购中心手工登记发票（无正式税务台账时兼容计入）",
            "status": invoice.status or "received",
            "redAdjusted": False,
            "needsReview": False,
            "reviewReason": "",
        })
    return entries, total, reasons


def compatibility_invoice_status(status: str) -> str:
    """ExternalPurchaseOrder.invoice_status 的只读兼容缓存映射。

    新代码不得把该字段当事实源；仅在旧接口仍需要返回历史枚举时同步缓存。
    """
    return {
        "done": "full",
        "partial": "partial",
        "pending": "unverified",
        "none": "none",
        "needs_review": "unverified",
    }.get(status, "unverified")


def purchase_invoice_truth(
    db: Session,
    *,
    order: Alibaba1688Order | None = None,
    external: ExternalPurchaseOrder | None = None,
    prefetch: Any | None = None,
) -> dict:
    """返回一笔逻辑采购订单的唯一发票结论。"""
    refs = _logical_refs(db, order, external, prefetch=prefetch)
    tax_entries, tax_total, reasons, tax_numbers = _tax_entries(
        db, refs, prefetch=prefetch
    )
    legacy_entries, legacy_total, legacy_reasons = _legacy_entries(
        db, external, tax_numbers, prefetch=prefetch
    )
    for reason in legacy_reasons:
        if reason not in reasons:
            reasons.append(reason)

    entries = tax_entries + legacy_entries
    total = tax_total + legacy_total
    target = _target_amount(order, external)
    outstanding: Decimal | None = None
    if target is None:
        status = "needs_review" if reasons else "none"
    else:
        outstanding = max(target - total, Decimal("0"))
        if total > target + EPS:
            reason = f"有效发票合计 {total} 超过应开票金额 {target}"
            if reason not in reasons:
                reasons.append(reason)
        if reasons:
            status = "needs_review"
        elif total <= EPS:
            status = "pending"
        elif outstanding <= EPS:
            status = "done"
        else:
            status = "partial"

    return {
        "status": status,
        "invoicedAmount": total,
        "outstandingAmount": outstanding,
        "targetAmount": target,
        "entries": entries,
        "taxInvoiceCount": sum(1 for row in tax_entries if row["invoiceKind"] == "tax"),
        "legacyInvoiceCount": len(legacy_entries),
        "needsReview": bool(reasons),
        "reviewReasons": reasons,
        "verified": bool(entries) and all(bool(row.get("verified")) for row in entries),
    }
