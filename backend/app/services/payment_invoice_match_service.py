"""付款↔发票匹配标记清单（财务月度资料）。

- 银行付款（bank_transactions.direction="out"）↔ 对方开来的进项发票（tax_invoices.direction="input"）
- 关联复用 tax_invoice_links（target_type="bank_transaction"）；
  自动匹配必须经过“供应商/账号 + 金额 + 唯一最佳日期”判定，歧义候选不落库；
  人工确认与人工拒绝始终高于自动规则。
- 银行付款核对状态只从 target_type="bank_transaction" 的 TaxInvoiceLink 推导；
  绝不读写 TaxInvoice.match_status / match_note，避免污染采购/销售业务匹配状态。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.audit import audit
from app.models.bank import BankAccount, BankTransaction
from app.models.business_partner import BusinessPartner, BusinessPartnerIdentifier, BusinessPartnerLink
from app.models.purchase import Supplier
from app.models.tax import TaxInvoice, TaxInvoiceLink
from app.services import tax_invoice_service
from app.services.monthly_core import month_bounds
from app.services.payment_invoice_match_helpers import (
    TARGET_TYPE,
    TOLERANCE,
    _dec,
    _invoice_bank_allocated,
    _invoice_brief,
    _invoice_candidate_window,
    _invoice_issue_date,
    _invoice_target_amount,
    _link_amount,
    _month_range,
    _normalize_account,
    _normalize_name,
    _normalize_tax_no,
    _status,
    _txn_bank_allocated,
)
from app.utils.money import to_decimal

AUTO_REPAIR_NEAR_DAYS = 7
AUTO_REPAIR_STALE_DAYS = 30
AUTO_REASSIGNABLE_METHODS = {
    "auto",
    "supplier_account",
    "auto_date",
    "supplier_account_date",
    "business_partner_date",
    "auto_reassigned",
}


def _supplier_accounts_by_name(db: Session) -> dict[str, set[str]]:
    """构建可信“发票销方名称 → 银行账号”证据池。

    除旧 Supplier 主档外，还会使用统一往来单位里维护的多个银行账号，并从
    已确认历史票款中学习稳定的“税号 + 银行账号”关系。历史学习至少要求同一
    税号/账号出现 2 次，且该账号没有对应过其他税号，避免一次误配被放大。
    """
    result: dict[str, set[str]] = {}

    # 旧 Supplier 主档继续兼容。
    for supplier in db.query(Supplier).all():
        name = _normalize_name(supplier.name)
        account = _normalize_account(supplier.bank_account_no)
        if name and account:
            result.setdefault(name, set()).add(account)

    active_partners = {
        int(row.id): row
        for row in db.query(BusinessPartner).filter(BusinessPartner.status == "active").all()
    }
    partner_taxes: dict[int, set[str]] = {}
    partner_accounts: dict[int, set[str]] = {}
    for partner_id, partner in active_partners.items():
        tax_no = _normalize_tax_no(partner.tax_no)
        account = _normalize_account(partner.bank_account_no)
        if tax_no:
            partner_taxes.setdefault(partner_id, set()).add(tax_no)
        if account:
            partner_accounts.setdefault(partner_id, set()).add(account)

    for identifier in db.query(BusinessPartnerIdentifier).all():
        partner_id = int(identifier.partner_id)
        if partner_id not in active_partners:
            continue
        if identifier.kind == "tax_no":
            value = _normalize_tax_no(identifier.normalized_value or identifier.value)
            if value:
                partner_taxes.setdefault(partner_id, set()).add(value)
        elif identifier.kind == "bank_account":
            value = _normalize_account(identifier.normalized_value or identifier.value)
            if value:
                partner_accounts.setdefault(partner_id, set()).add(value)

    tax_to_partner_ids: dict[str, set[int]] = {}
    for partner_id, taxes in partner_taxes.items():
        for tax_no in taxes:
            tax_to_partner_ids.setdefault(tax_no, set()).add(partner_id)

    # 只从强确认关系学习，纯名称自动匹配不参与“训练”。
    strong_methods = {"manual", "business_partner_date", "supplier_account_date", "supplier_account"}
    confirmed_links = (
        db.query(TaxInvoiceLink)
        .filter(
            TaxInvoiceLink.target_type == TARGET_TYPE,
            TaxInvoiceLink.confirmed.is_(True),
            TaxInvoiceLink.match_method.in_(strong_methods),
        )
        .all()
    )
    invoice_ids = {int(row.invoice_id) for row in confirmed_links}
    txn_ids = {int(row.target_id) for row in confirmed_links}
    invoices = {
        int(row.id): row
        for row in db.query(TaxInvoice).filter(TaxInvoice.id.in_(invoice_ids)).all()
    } if invoice_ids else {}
    txns = {
        int(row.id): row
        for row in db.query(BankTransaction).filter(BankTransaction.id.in_(txn_ids)).all()
    } if txn_ids else {}

    pair_hits: dict[tuple[str, str], set[tuple[int, int]]] = {}
    account_taxes: dict[str, set[str]] = {}
    for link in confirmed_links:
        invoice = invoices.get(int(link.invoice_id))
        txn = txns.get(int(link.target_id))
        if invoice is None or txn is None:
            continue
        tax_no = _normalize_tax_no(invoice.seller_tax_id)
        account = _normalize_account(txn.counterparty_account)
        if not tax_no or not account:
            continue
        pair_hits.setdefault((tax_no, account), set()).add((invoice.id, txn.id))
        account_taxes.setdefault(account, set()).add(tax_no)

    learned_by_tax: dict[str, set[str]] = {}
    for (tax_no, account), observations in pair_hits.items():
        if len(observations) >= 2 and len(account_taxes.get(account, set())) == 1:
            learned_by_tax.setdefault(tax_no, set()).add(account)

    # 将“税号 → 账号”画像映射回当前发票销方名称，继续复用既有安全匹配流程：
    # 金额一致 + 唯一最佳日期仍然必须满足。
    for invoice in db.query(TaxInvoice).filter(TaxInvoice.direction == "input").all():
        name = _normalize_name(invoice.seller_name)
        tax_no = _normalize_tax_no(invoice.seller_tax_id)
        if not name or not tax_no:
            continue

        partner_ids = tax_to_partner_ids.get(tax_no, set())
        if len(partner_ids) == 1:
            partner_id = next(iter(partner_ids))
            for account in partner_accounts.get(partner_id, set()):
                result.setdefault(name, set()).add(account)

        for account in learned_by_tax.get(tax_no, set()):
            result.setdefault(name, set()).add(account)

    return result


def _linked_partner_ids(
    db: Session,
    *,
    source_type: str,
    source_ids: list[int],
    relation_role: str,
) -> dict[int, int]:
    """返回来源事实 → 统一往来主体。

    V2 优先读取业务事实自己的 partner FK；BusinessPartnerLink 仅作为兼容回退与
    审计证据。这样付款核对不再依赖每次重新做名称/账号归档。
    """
    if not source_ids:
        return {}

    result: dict[int, int] = {}
    if source_type == "tax_invoice":
        attr = (
            TaxInvoice.seller_partner_id
            if relation_role == "seller"
            else TaxInvoice.buyer_partner_id
            if relation_role == "buyer"
            else None
        )
        if attr is not None:
            rows = (
                db.query(TaxInvoice.id, attr)
                .filter(TaxInvoice.id.in_(source_ids), attr.isnot(None))
                .all()
            )
            result.update({int(source_id): int(partner_id) for source_id, partner_id in rows})
    elif source_type == "bank_transaction" and relation_role == "counterparty":
        rows = (
            db.query(BankTransaction.id, BankTransaction.counterparty_partner_id)
            .filter(
                BankTransaction.id.in_(source_ids),
                BankTransaction.counterparty_partner_id.isnot(None),
            )
            .all()
        )
        result.update({int(source_id): int(partner_id) for source_id, partner_id in rows})

    missing = [source_id for source_id in source_ids if source_id not in result]
    if not missing:
        return result

    rows = (
        db.query(BusinessPartnerLink)
        .filter(
            BusinessPartnerLink.source_type == source_type,
            BusinessPartnerLink.source_id.in_(missing),
            BusinessPartnerLink.relation_role == relation_role,
            BusinessPartnerLink.status == "linked",
            BusinessPartnerLink.partner_id.isnot(None),
        )
        .all()
    )
    result.update(
        {int(row.source_id): int(row.partner_id) for row in rows if row.partner_id is not None}
    )
    return result


def _candidate_evidence(
    txn: BankTransaction,
    invoice: TaxInvoice,
    supplier_accounts: dict[str, set[str]],
    invoice_partner_ids: dict[int, int] | None = None,
    txn_partner_ids: dict[int, int] | None = None,
) -> dict[str, Any]:
    invoice_name = _normalize_name(invoice.seller_name)
    txn_name = _normalize_name(txn.counterparty_name)
    txn_account = _normalize_account(getattr(txn, "counterparty_account", ""))
    account_match = bool(
        txn_account
        and invoice_name
        and txn_account in supplier_accounts.get(invoice_name, set())
    )
    name_match = bool(invoice_name and txn_name and invoice_name == txn_name)
    invoice_partner_id = (invoice_partner_ids or {}).get(invoice.id)
    txn_partner_id = (txn_partner_ids or {}).get(txn.id)
    partner_match = bool(
        invoice_partner_id
        and txn_partner_id
        and invoice_partner_id == txn_partner_id
    )
    issue_date = _invoice_issue_date(invoice)
    distance = abs((issue_date - txn.txn_date).days) if issue_date and txn.txn_date else 10**9
    identity_rank = 0 if partner_match else 1 if account_match else 2
    return {
        "nameMatch": name_match,
        "accountMatch": account_match,
        "partnerMatch": partner_match,
        "partnerId": invoice_partner_id if partner_match else None,
        "dateDistanceDays": distance,
        # 已确认统一往来单位是最强身份事实；供应商账号其次；原始名称最后。
        "score": (identity_rank, distance),
    }


def _choose_unique_best_candidate(
    txn: BankTransaction,
    candidates: list[TaxInvoice],
    supplier_accounts: dict[str, set[str]],
    invoice_partner_ids: dict[int, int] | None = None,
    txn_partner_ids: dict[int, int] | None = None,
) -> tuple[TaxInvoice | None, dict[str, Any]]:
    scored: list[tuple[tuple[int, int], TaxInvoice, dict[str, Any]]] = []
    for invoice in candidates:
        evidence = _candidate_evidence(
            txn,
            invoice,
            supplier_accounts,
            invoice_partner_ids,
            txn_partner_ids,
        )
        if not (evidence["nameMatch"] or evidence["accountMatch"] or evidence["partnerMatch"]):
            continue
        scored.append((evidence["score"], invoice, evidence))
    if not scored:
        return None, {"candidateCount": 0, "ambiguous": False}
    scored.sort(key=lambda item: (item[0], item[1].id))
    best_score = scored[0][0]
    tied = [item for item in scored if item[0] == best_score]
    if len(tied) != 1:
        return None, {
            "candidateCount": len(scored),
            "ambiguous": True,
            "bestScore": list(best_score),
            "candidateInvoiceIds": [item[1].id for item in tied],
        }
    _, invoice, evidence = tied[0]
    return invoice, {
        **evidence,
        "candidateCount": len(scored),
        "ambiguous": False,
        "candidateInvoiceIds": [item[1].id for item in scored],
    }


def _repair_wrong_auto_links(
    db: Session,
    *,
    txns: list[BankTransaction],
    invoices: list[TaxInvoice],
    supplier_accounts: dict[str, set[str]],
    invoice_partner_ids: dict[int, int] | None = None,
    txn_partner_ids: dict[int, int] | None = None,
) -> list[dict[str, Any]]:
    """只纠正“整笔一对一”的系统自动关联；人工/拆分关系绝不自动搬家。"""
    if not txns or not invoices:
        return []
    txn_map = {txn.id: txn for txn in txns}
    txn_ids = list(txn_map)
    rows = (
        db.query(TaxInvoiceLink)
        .filter(
            TaxInvoiceLink.target_type == TARGET_TYPE,
            TaxInvoiceLink.target_id.in_(txn_ids),
        )
        .all()
    )
    by_txn: dict[int, list[TaxInvoiceLink]] = {}
    occupied_pairs = {(row.invoice_id, row.target_id) for row in rows}
    for row in rows:
        by_txn.setdefault(row.target_id, []).append(row)

    repairs: list[dict[str, Any]] = []
    for txn in txns:
        txn_rows = by_txn.get(txn.id, [])
        active = [row for row in txn_rows if row.confirmed and row.match_method != "rejected"]
        # 一旦有人工作为依据，或存在拆分/多关联，不自动改。
        if any(row.match_method not in AUTO_REASSIGNABLE_METHODS for row in active):
            continue
        if len(active) != 1:
            continue
        current_link = active[0]
        if current_link.match_method not in AUTO_REASSIGNABLE_METHODS:
            continue
        current_invoice = db.get(TaxInvoice, current_link.invoice_id)
        if current_invoice is None:
            continue
        link_amount = _link_amount(current_link, current_invoice)
        txn_total = _dec(txn.amount)
        if abs(link_amount - txn_total) > TOLERANCE:
            continue

        candidates: list[TaxInvoice] = []
        for invoice in invoices:
            pair = (invoice.id, txn.id)
            if pair in occupied_pairs and invoice.id != current_invoice.id:
                # 包含人工拒绝：任何历史 pair 都不能被自动复活/覆盖。
                continue
            evidence = _candidate_evidence(
                txn,
                invoice,
                supplier_accounts,
                invoice_partner_ids,
                txn_partner_ids,
            )
            if not (evidence["nameMatch"] or evidence["accountMatch"] or evidence["partnerMatch"]):
                continue
            used = _invoice_bank_allocated(
                db,
                invoice.id,
                exclude_link_id=current_link.id if invoice.id == current_invoice.id else None,
            )
            remaining = _invoice_target_amount(db, invoice) - used
            if abs(remaining - txn_total) <= TOLERANCE:
                candidates.append(invoice)

        best, best_evidence = _choose_unique_best_candidate(
            txn,
            candidates,
            supplier_accounts,
            invoice_partner_ids,
            txn_partner_ids,
        )
        if best is None or best.id == current_invoice.id:
            continue
        current_evidence = _candidate_evidence(
            txn,
            current_invoice,
            supplier_accounts,
            invoice_partner_ids,
            txn_partner_ids,
        )
        if tuple(best_evidence.get("score") or (99, 10**9)) >= tuple(current_evidence["score"]):
            continue
        # 历史账自动搬家只处理“明显错配”：账号必须一致，新票很近，旧票明显跨期过远。
        if not (best_evidence.get("accountMatch") or best_evidence.get("partnerMatch")):
            continue
        if int(best_evidence.get("dateDistanceDays") or 10**9) > AUTO_REPAIR_NEAR_DAYS:
            continue
        if int(current_evidence.get("dateDistanceDays") or 0) < AUTO_REPAIR_STALE_DAYS:
            continue

        old_note = current_link.note or ""
        current_link.match_method = "rejected"
        current_link.confirmed = False
        current_link.confidence = None
        current_link.note = (
            "系统纠偏：原自动关联不是唯一最佳候选；"
            f"由发票#{current_invoice.id}调整至#{best.id}。原备注：{old_note}"
        )
        method = (
            "business_partner_date"
            if best_evidence.get("partnerMatch")
            else "supplier_account_date"
            if best_evidence.get("accountMatch")
            else "auto_date"
        )
        replacement = TaxInvoiceLink(
            invoice_id=best.id,
            target_type=TARGET_TYPE,
            target_id=txn.id,
            allocated_amount=txn_total,
            match_method="auto_reassigned",
            confidence=Decimal("0.99") if best_evidence.get("accountMatch") else Decimal("0.95"),
            confirmed=True,
            note=(
                f"系统纠偏自动匹配：method={method}；"
                f"候选{best_evidence.get('candidateCount', 0)}张；"
                f"日期差{best_evidence.get('dateDistanceDays')}天"
            ),
        )
        db.add(replacement)
        db.flush()
        occupied_pairs.add((best.id, txn.id))
        repairs.append({
            "txnId": txn.id,
            "txnDate": txn.txn_date.isoformat(),
            "amount": str(txn_total),
            "fromInvoiceId": current_invoice.id,
            "fromInvoiceNumber": current_invoice.invoice_number,
            "toInvoiceId": best.id,
            "toInvoiceNumber": best.invoice_number,
            "accountMatched": bool(best_evidence.get("accountMatch")),
            "dateDistanceDays": best_evidence.get("dateDistanceDays"),
            "candidateCount": best_evidence.get("candidateCount"),
        })
    return repairs


def overview(db: Session, year: int, month: int) -> dict[str, Any]:
    """当月银行付款清单 + 已挂发票 + 当月进项发票池 + 汇总（推导，不落库）。"""
    start, end = _month_range(year, month)
    invoice_start, invoice_end = month_bounds(year, month)

    txns = (
        db.query(BankTransaction)
        .filter(
            BankTransaction.txn_date >= start,
            BankTransaction.txn_date <= end,
            BankTransaction.direction == "out",
        )
        .order_by(BankTransaction.txn_date, BankTransaction.id)
        .all()
    )
    txn_ids = [txn.id for txn in txns]
    account_ids = {txn.account_id for txn in txns if txn.account_id is not None}
    account_map = {
        row.id: row
        for row in db.query(BankAccount).filter(BankAccount.id.in_(account_ids)).all()
    } if account_ids else {}

    links: list[TaxInvoiceLink] = []
    if txn_ids:
        links = (
            db.query(TaxInvoiceLink)
            .filter(
                TaxInvoiceLink.target_type == TARGET_TYPE,
                TaxInvoiceLink.target_id.in_(txn_ids),
                TaxInvoiceLink.confirmed.is_(True),
                TaxInvoiceLink.match_method != "rejected",
            )
            .all()
        )
    invoice_ids = {link.invoice_id for link in links}
    invoices = (
        db.query(TaxInvoice).filter(TaxInvoice.id.in_(invoice_ids)).all()
        if invoice_ids
        else []
    )
    invoice_map = {row.id: row for row in invoices}
    links_by_txn: dict[int, list[TaxInvoiceLink]] = {}
    for link in links:
        links_by_txn.setdefault(link.target_id, []).append(link)

    payments: list[dict[str, Any]] = []
    matched_total = Decimal("0.0000")
    payment_total = Decimal("0.0000")
    counts = {"matched": 0, "partial": 0, "unmatched": 0}
    for txn in txns:
        amount = _dec(txn.amount)
        payment_total += amount
        txn_links = sorted(links_by_txn.get(txn.id, []), key=lambda row: row.id)
        briefs: list[dict[str, Any]] = []
        matched_amount = Decimal("0.0000")
        for link in txn_links:
            invoice = invoice_map.get(link.invoice_id)
            if invoice is None:
                continue
            matched_amount += _link_amount(link, invoice)
            briefs.append(_invoice_brief(invoice, link))
        matched_amount = min(matched_amount, amount) if amount else matched_amount
        remaining = amount - matched_amount
        status = _status(remaining, amount)
        counts[status] += 1
        matched_total += matched_amount
        account = account_map.get(txn.account_id)
        payments.append({
            "id": txn.id,
            "txnDate": txn.txn_date.isoformat(),
            "counterpartyName": txn.counterparty_name or "",
            "counterpartyAccount": txn.counterparty_account or "",
            "amount": str(amount),
            "serialNo": txn.serial_no or "",
            "voucherNo": txn.voucher_no or "",
            "summary": txn.summary or "",
            "accountNo": account.account_no if account else "",
            "accountName": account.account_name if account else "",
            "invoices": briefs,
            "matchedAmount": str(_dec(matched_amount)),
            "remaining": str(_dec(remaining)),
            "status": status,
            "suggestedInvoiceIds": [],
        })

    # 当月进项发票池：只统计 bank_transaction 链接口径，不与采购 FIFO 混算
    pool_rows = (
        db.query(TaxInvoice)
        .filter(
            TaxInvoice.direction == "input",
            TaxInvoice.issue_date >= invoice_start,
            TaxInvoice.issue_date < invoice_end,
        )
        .order_by(TaxInvoice.issue_date, TaxInvoice.id)
        .all()
    )
    # 红冲、作废、待确认和非正数金额发票仍保留在发票/会计模块，
    # 但不能进入“银行付款核对”池，更不能因金额 <= 0 被推导成 matched。
    pool_rows = [row for row in pool_rows if tax_invoice_service.is_bank_payment_reconciliation_eligible(row, db=db)]
    pool_ids = [row.id for row in pool_rows]
    pool_links: list[TaxInvoiceLink] = []
    if pool_ids:
        pool_links = (
            db.query(TaxInvoiceLink)
            .filter(
                TaxInvoiceLink.target_type == TARGET_TYPE,
                TaxInvoiceLink.invoice_id.in_(pool_ids),
                TaxInvoiceLink.confirmed.is_(True),
                TaxInvoiceLink.match_method != "rejected",
            )
            .all()
        )
    pool_txn_ids = {link.target_id for link in pool_links}
    pool_link_txns = (
        db.query(BankTransaction).filter(BankTransaction.id.in_(pool_txn_ids)).all()
        if pool_txn_ids
        else []
    )
    pool_txn_map = {row.id: row for row in pool_link_txns}
    linked_account_ids = {row.account_id for row in pool_link_txns if row.account_id is not None}
    missing_account_ids = linked_account_ids.difference(account_map)
    if missing_account_ids:
        for account in db.query(BankAccount).filter(BankAccount.id.in_(missing_account_ids)).all():
            account_map[account.id] = account
    linked_by_invoice: dict[int, list[TaxInvoiceLink]] = {}
    for link in pool_links:
        linked_by_invoice.setdefault(link.invoice_id, []).append(link)

    invoice_bank_context = tax_invoice_service._invoice_bank_payment_context(db, pool_rows)
    invoice_pool: list[dict[str, Any]] = []
    pool_by_id: dict[int, dict[str, Any]] = {}
    for invoice in pool_rows:
        total = _invoice_target_amount(db, invoice)
        linked_amount = Decimal("0.0000")
        briefs: list[dict[str, Any]] = []
        for link in sorted(linked_by_invoice.get(invoice.id, []), key=lambda row: row.id):
            linked_amount += _link_amount(link, invoice)
            txn = pool_txn_map.get(link.target_id)
            account = account_map.get(txn.account_id) if txn else None
            briefs.append({
                **_invoice_brief(invoice, link),
                "txnId": txn.id if txn else None,
                "txnDate": txn.txn_date.isoformat() if txn else "",
                "txnAmount": str(_dec(txn.amount)) if txn else "0.00",
                "counterpartyName": txn.counterparty_name if txn else "",
                "counterpartyAccount": txn.counterparty_account if txn else "",
                "serialNo": txn.serial_no if txn else "",
                "voucherNo": txn.voucher_no if txn else "",
                "summary": txn.summary if txn else "",
                "accountNo": account.account_no if account else "",
                "accountName": account.account_name if account else "",
            })
        derived_bank = invoice_bank_context.get(invoice.id, {})
        overpaid = _dec(derived_bank.get("bankOverpaidAmount", max(linked_amount - total, Decimal("0"))))
        overpaid_settled = _dec(derived_bank.get("bankOverpaidSettledAmount"))
        overpaid_unsettled = _dec(derived_bank.get("bankOverpaidUnsettledAmount", overpaid))
        remaining = _dec(derived_bank.get("bankRemainingAmount", max(total - linked_amount, Decimal("0"))))
        bank_status = str(derived_bank.get("bankPaymentStatus") or _status(remaining, total))
        row = {
            "id": invoice.id,
            "invoiceNumber": invoice.invoice_number,
            "sellerName": invoice.seller_name,
            "issueDate": invoice.issue_date.isoformat()[:10] if invoice.issue_date else "",
            "totalAmount": str(total),
            "bankLinkedAmount": str(_dec(linked_amount)),
            "remaining": str(_dec(remaining)),
            "overpaidAfterRedAmount": str(_dec(overpaid)),
            "overpaidSettledAmount": str(_dec(overpaid_settled)),
            "overpaidUnsettledAmount": str(_dec(overpaid_unsettled)),
            "bankMatchStatus": bank_status,
            "links": briefs,
            "suggested": False,
            "suggestedPaymentIds": [],
        }
        invoice_pool.append(row)
        pool_by_id[invoice.id] = row

    # 建议与自动匹配共用“统一往来单位 > 账号 > 原始名称 + 日期最近 + 唯一最佳”口径。
    supplier_accounts = _supplier_accounts_by_name(db)
    txn_by_id = {txn.id: txn for txn in txns}
    pool_invoice_map = {invoice.id: invoice for invoice in pool_rows}
    invoice_partner_ids = _linked_partner_ids(
        db,
        source_type="tax_invoice",
        source_ids=list(pool_invoice_map),
        relation_role="seller",
    )
    txn_partner_ids = _linked_partner_ids(
        db,
        source_type="bank_transaction",
        source_ids=list(txn_by_id),
        relation_role="counterparty",
    )
    for payment in payments:
        remaining = to_decimal(payment["remaining"]) or Decimal("0.0000")
        if remaining <= TOLERANCE:
            continue
        txn = txn_by_id.get(payment["id"])
        if txn is None:
            continue
        candidate_invoices = [
            pool_invoice_map[row["id"]]
            for row in invoice_pool
            if row["id"] in pool_invoice_map
            and abs(_dec(row["remaining"]) - _dec(remaining)) <= TOLERANCE
        ]
        invoice, evidence = _choose_unique_best_candidate(
            txn,
            candidate_invoices,
            supplier_accounts,
            invoice_partner_ids,
            txn_partner_ids,
        )
        if invoice is None:
            if evidence.get("ambiguous"):
                payment["suggestionBlockedReason"] = "存在并列的最佳候选，需人工确认"
                payment["suggestedCandidateInvoiceIds"] = evidence.get("candidateInvoiceIds", [])
            continue
        row = pool_by_id.get(invoice.id)
        if row is None:
            continue
        row["suggested"] = True
        row["suggestedPaymentIds"].append(payment["id"])
        row["suggestionEvidence"] = {
            "accountMatched": bool(evidence.get("accountMatch")),
            "partnerMatched": bool(evidence.get("partnerMatch")),
            "dateDistanceDays": evidence.get("dateDistanceDays"),
            "candidateCount": evidence.get("candidateCount"),
        }
        payment["suggestedInvoiceIds"].append(row["id"])

    invoice_total = sum((_dec(row["totalAmount"]) for row in invoice_pool), Decimal("0.0000"))
    invoice_matched_total = sum(
        (min(_dec(row["bankLinkedAmount"]), _dec(row["totalAmount"])) for row in invoice_pool),
        Decimal("0.0000"),
    )
    invoice_overpaid_total = sum(
        (_dec(row.get("overpaidUnsettledAmount")) for row in invoice_pool),
        Decimal("0.0000"),
    )
    invoice_historical_overpaid_total = sum(
        (_dec(row.get("overpaidAfterRedAmount")) for row in invoice_pool),
        Decimal("0.0000"),
    )
    invoice_settled_overpaid_total = sum(
        (_dec(row.get("overpaidSettledAmount")) for row in invoice_pool),
        Decimal("0.0000"),
    )
    invoice_counts = {
        "matched": sum(1 for row in invoice_pool if row["bankMatchStatus"] == "matched"),
        "partial": sum(1 for row in invoice_pool if row["bankMatchStatus"] == "partial"),
        "unmatched": sum(1 for row in invoice_pool if row["bankMatchStatus"] == "unmatched"),
        "overpaid_after_red": sum(1 for row in invoice_pool if row["bankMatchStatus"] == "overpaid_after_red"),
        "red_overpayment_settled": sum(1 for row in invoice_pool if row["bankMatchStatus"] == "red_overpayment_settled"),
    }

    return {
        "year": year,
        "month": month,
        "payments": payments,
        "invoicePool": invoice_pool,
        "summary": {
            "paymentTotal": str(_dec(payment_total)),
            "matchedTotal": str(_dec(matched_total)),
            "unmatchedTotal": str(_dec(payment_total - matched_total)),
            "txnCount": len(payments),
            "matchedCount": counts["matched"],
            "partialCount": counts["partial"],
            "unmatchedCount": counts["unmatched"],
            "invoiceTotal": str(_dec(invoice_total)),
            "invoiceMatchedTotal": str(_dec(invoice_matched_total)),
            "invoiceOutstandingTotal": str(_dec(max(invoice_total - invoice_matched_total, Decimal("0")))),
            "invoiceOverpaidAfterRedTotal": str(_dec(invoice_overpaid_total)),
            "invoiceHistoricalOverpaidAfterRedTotal": str(_dec(invoice_historical_overpaid_total)),
            "invoiceSettledOverpaidAfterRedTotal": str(_dec(invoice_settled_overpaid_total)),
            "invoiceCount": len(invoice_pool),
            "invoiceMatchedCount": invoice_counts["matched"],
            "invoicePartialCount": invoice_counts["partial"],
            "invoiceUnmatchedCount": invoice_counts["unmatched"],
            "invoiceOverpaidAfterRedCount": invoice_counts["overpaid_after_red"],
            "invoiceResolvedRedOverpaymentCount": invoice_counts["red_overpayment_settled"],
        },
    }


def txn_reconciliation_statuses(db: Session, txn_ids: list[int]) -> dict[int, dict[str, Any]]:
    """按确认分摊金额返回银行支出流水的核对状态。"""
    if not txn_ids:
        return {}
    txns = db.query(BankTransaction).filter(BankTransaction.id.in_(txn_ids)).all()
    txn_map = {row.id: row for row in txns if row.direction == "out"}
    if not txn_map:
        return {}

    links = (
        db.query(TaxInvoiceLink)
        .filter(
            TaxInvoiceLink.target_type == TARGET_TYPE,
            TaxInvoiceLink.target_id.in_(list(txn_map)),
            TaxInvoiceLink.confirmed.is_(True),
            TaxInvoiceLink.match_method != "rejected",
        )
        .all()
    )
    invoice_ids = {row.invoice_id for row in links if row.allocated_amount is None}
    invoice_map = {
        row.id: row
        for row in db.query(TaxInvoice).filter(TaxInvoice.id.in_(invoice_ids)).all()
    } if invoice_ids else {}

    allocated_by_txn: dict[int, Decimal] = {}
    matched_at_by_txn: dict[int, Any] = {}
    for link in links:
        allocated_by_txn[link.target_id] = (
            allocated_by_txn.get(link.target_id, Decimal("0"))
            + _link_amount(link, invoice_map.get(link.invoice_id))
        )
        link_time = link.updated_at or link.created_at
        current_time = matched_at_by_txn.get(link.target_id)
        if link_time is not None and (current_time is None or link_time > current_time):
            matched_at_by_txn[link.target_id] = link_time

    result: dict[int, dict[str, Any]] = {}
    for txn_id, txn in txn_map.items():
        total = _dec(txn.amount)
        allocated = min(allocated_by_txn.get(txn_id, Decimal("0")), total)
        remaining = total - allocated
        matched_at = matched_at_by_txn.get(txn_id)
        result[txn_id] = {
            "allocatedAmount": str(_dec(allocated)),
            "remainingAmount": str(_dec(remaining)),
            "status": _status(remaining, total),
            "matchedAt": matched_at.isoformat() if matched_at is not None else None,
        }
    return result


def fully_reconciled_txn_ids(db: Session, txn_ids: list[int]) -> set[int]:
    """返回银行付款金额已被确认发票分摊完整覆盖的支出流水 ID。"""
    statuses = txn_reconciliation_statuses(db, txn_ids)
    return {txn_id for txn_id, row in statuses.items() if row["status"] == "matched"}


def pending_invoices(db: Session, limit: int = 500) -> list[dict[str, Any]]:
    """跨账期返回仍有银行付款待核对余额的有效进项发票。

    只使用 bank_transaction 链接计算已核对金额；采购/销售 match_status 与本列表完全无关。
    """
    limit = max(1, min(int(limit), 500))
    rows = (
        db.query(TaxInvoice)
        .filter(
            TaxInvoice.direction == "input",
            TaxInvoice.status == "issued",
        )
        .order_by(TaxInvoice.issue_date.desc(), TaxInvoice.id.desc())
        .all()
    )
    rows = [row for row in rows if tax_invoice_service.is_bank_payment_reconciliation_eligible(row, db=db)]
    invoice_ids = [row.id for row in rows]
    allocated_by_invoice: dict[int, Decimal] = {}
    if invoice_ids:
        links = (
            db.query(TaxInvoiceLink)
            .filter(
                TaxInvoiceLink.target_type == TARGET_TYPE,
                TaxInvoiceLink.invoice_id.in_(invoice_ids),
                TaxInvoiceLink.confirmed.is_(True),
                TaxInvoiceLink.match_method != "rejected",
            )
            .all()
        )
        invoice_map = {row.id: row for row in rows}
        for link in links:
            allocated_by_invoice[link.invoice_id] = (
                allocated_by_invoice.get(link.invoice_id, Decimal("0"))
                + _link_amount(link, invoice_map.get(link.invoice_id))
            )

    result: list[dict[str, Any]] = []
    for invoice in rows:
        total = _invoice_target_amount(db, invoice)
        linked = allocated_by_invoice.get(invoice.id, Decimal("0"))
        remaining = total - linked
        if remaining <= TOLERANCE:
            continue
        result.append({
            "id": invoice.id,
            "invoiceNumber": invoice.invoice_number,
            "sellerName": invoice.seller_name,
            "issueDate": invoice.issue_date.isoformat()[:10] if invoice.issue_date else "",
            "totalAmount": str(total),
            "bankLinkedAmount": str(_dec(linked)),
            "remaining": str(_dec(remaining)),
            "bankMatchStatus": _status(remaining, total),
        })
        if len(result) >= limit:
            break
    return result


def _split_match_invoice_to_txns(
    db: Session, invoice: TaxInvoice, txns: list[BankTransaction],
    actor: str, target_type: str = "bank_transaction",
    allocated_by_invoice: dict[int, Decimal] | None = None,
    allocated_by_txn: dict[int, Decimal] | None = None,
) -> tuple[int, Decimal]:
    """把一张发票拆给多笔流水（合计金额相等）。

    例：发票 ¥60288，被流水 ¥3306 + ¥5402 + ... 分次付完。
    返回 (匹配流水数, 已分配金额)。
    """
    target = _invoice_target_amount(db, invoice)
    allocated = (
        allocated_by_invoice.get(invoice.id, Decimal("0.0000"))
        if allocated_by_invoice is not None
        else _invoice_bank_allocated(db, invoice.id)
    )
    if not tax_invoice_service.is_bank_payment_reconciliation_eligible(invoice, db=db):
        return 0, allocated
    count = 0
    for txn in txns:
        if target - allocated <= TOLERANCE:
            break
        txn_amount = _dec(txn.amount)
        txn_used = (
            allocated_by_txn.get(txn.id, Decimal("0.0000"))
            if allocated_by_txn is not None
            else _txn_bank_allocated(db, txn.id)
        )
        txn_remaining = txn_amount - txn_used
        if txn_remaining <= TOLERANCE:
            continue
        existing = (
            db.query(TaxInvoiceLink)
            .filter_by(invoice_id=invoice.id, target_type=target_type, target_id=txn.id)
            .first()
        )
        # 已存在（包括 rejected）就不自动复活；人工拒绝必须继续受尊重。
        if existing is not None:
            continue
        portion = min(target - allocated, txn_remaining)
        if portion <= TOLERANCE:
            continue
        link_row = TaxInvoiceLink(
            invoice_id=invoice.id,
            target_type=target_type,
            target_id=txn.id,
            allocated_amount=portion,
            match_method="auto_split",
            confidence=Decimal("0.85"),
            confirmed=True,
            note=f"系统拆分匹配：发票 ¥{_dec(invoice.total_amount)} 拆给多笔流水",
        )
        db.add(link_row)
        db.flush()
        allocated += portion
        if allocated_by_invoice is not None:
            allocated_by_invoice[invoice.id] = allocated
        if allocated_by_txn is not None:
            allocated_by_txn[txn.id] = txn_used + portion
        count += 1
    return count, allocated


def _split_match_txn_to_invoices(
    db: Session, txn: BankTransaction, invoices: list[TaxInvoice],
    actor: str, target_type: str = "bank_transaction",
    allocated_by_invoice: dict[int, Decimal] | None = None,
    allocated_by_txn: dict[int, Decimal] | None = None,
) -> tuple[int, Decimal]:
    """把一笔流水拆给多张发票（合计金额相等）。

    例：流水 ¥5000，被发票 ¥3080 + ¥1920 分次开完。
    返回 (匹配发票数, 已分配金额)。
    """
    target = _dec(txn.amount)
    allocated = (
        allocated_by_txn.get(txn.id, Decimal("0.0000"))
        if allocated_by_txn is not None
        else _txn_bank_allocated(db, txn.id)
    )
    count = 0
    for invoice in invoices:
        if target - allocated <= TOLERANCE:
            break
        if not tax_invoice_service.is_bank_payment_reconciliation_eligible(invoice, db=db):
            continue
        inv_amount = _invoice_target_amount(db, invoice)
        inv_used = (
            allocated_by_invoice.get(invoice.id, Decimal("0.0000"))
            if allocated_by_invoice is not None
            else _invoice_bank_allocated(db, invoice.id)
        )
        inv_remaining = inv_amount - inv_used
        if inv_remaining <= TOLERANCE:
            continue
        existing = (
            db.query(TaxInvoiceLink)
            .filter_by(invoice_id=invoice.id, target_type=target_type, target_id=txn.id)
            .first()
        )
        if existing is not None:
            continue
        portion = min(target - allocated, inv_remaining)
        if portion <= TOLERANCE:
            continue
        link_row = TaxInvoiceLink(
            invoice_id=invoice.id,
            target_type=target_type,
            target_id=txn.id,
            allocated_amount=portion,
            match_method="auto_split",
            confidence=Decimal("0.85"),
            confirmed=True,
            note=f"系统拆分匹配：流水 ¥{_dec(txn.amount)} 拆给多张发票",
        )
        db.add(link_row)
        db.flush()
        allocated += portion
        if allocated_by_txn is not None:
            allocated_by_txn[txn.id] = allocated
        if allocated_by_invoice is not None:
            allocated_by_invoice[invoice.id] = inv_used + portion
        count += 1
    return count, allocated


def auto_match_all_periods(db: Session, actor: str = "system") -> dict[str, Any]:
    """对所有历史银行支出账期执行一次安全的付款↔进项发票自动核对。

    这是“核对全部来源”使用的总入口。只按银行流水实际存在的年月遍历，
    每个月仍复用 auto_match 的安全边界：名称/账号证据、金额一致、唯一最近日期，
    歧义候选不落库，人工确认/拒绝不会被覆盖。
    """
    date_rows = (
        db.query(BankTransaction.txn_date)
        .filter(BankTransaction.direction == "out")
        .all()
    )
    periods = sorted({
        (value.year, value.month)
        for (value,) in date_rows
        if value is not None
    })

    results: list[dict[str, Any]] = []
    totals = {
        "matched": 0,
        "splitMatched": 0,
        "bigTxnSplitMatched": 0,
        "supplierMatched": 0,
        "repaired": 0,
        "ambiguous": 0,
        "skipped": 0,
    }
    for year, month in periods:
        result = auto_match(db, year=year, month=month, actor=actor)
        results.append(result)
        for key in totals:
            totals[key] += int(result.get(key, 0) or 0)

    created_links = (
        totals["matched"]
        + totals["splitMatched"]
        + totals["bigTxnSplitMatched"]
        + totals["supplierMatched"]
    )
    return {
        "periodCount": len(periods),
        "periods": [f"{year:04d}-{month:02d}" for year, month in periods],
        "matchedLinks": created_links,
        **totals,
        "results": results,
    }


def auto_match(db: Session, year: int, month: int, actor: str = "system") -> dict[str, Any]:
    """自动匹配当月银行付款 ↔ 进项发票。

    规则：先收集“同供应商/供应商账号 + 剩余金额相等”的全部候选，
    供应商银行账号为强证据，其次按付款日与开票日绝对距离排序；
    只有唯一最佳候选才自动落库。已有 manual/rejected/拆分链路不会被自动改写。
    """
    start, end = _month_range(year, month)
    invoice_window_start, invoice_window_end = _invoice_candidate_window(year, month)

    # 操作边界必须锁定当前账期：点“8 月自动匹配”只能修改 8 月银行支出。
    # 为兼容开票/付款跨月，只有候选发票允许向前后各扩 3 个完整自然月。
    txns = (
        db.query(BankTransaction)
        .filter(
            BankTransaction.txn_date >= start,
            BankTransaction.txn_date <= end,
            BankTransaction.direction == "out",
        )
        .order_by(BankTransaction.txn_date, BankTransaction.id)
        .all()
    )
    if not txns:
        return {"year": year, "month": month, "matched": 0, "skipped": 0, "details": []}

    # 候选进项发票允许跨月，但自动落库的银行流水仍严格属于当前账期。
    invoices = (
        db.query(TaxInvoice)
        .filter(
            TaxInvoice.direction == "input",
            TaxInvoice.issue_date >= invoice_window_start,
            TaxInvoice.issue_date < invoice_window_end,
        )
        .all()
    )
    invoices = [invoice for invoice in invoices if tax_invoice_service.is_bank_payment_reconciliation_eligible(invoice, db=db)]
    if not invoices:
        return {"year": year, "month": month, "matched": 0, "skipped": 0, "details": []}

    supplier_accounts = _supplier_accounts_by_name(db)
    invoice_partner_ids = _linked_partner_ids(
        db,
        source_type="tax_invoice",
        source_ids=[invoice.id for invoice in invoices],
        relation_role="seller",
    )
    txn_partner_ids = _linked_partner_ids(
        db,
        source_type="bank_transaction",
        source_ids=[txn.id for txn in txns],
        relation_role="counterparty",
    )
    repair_details = _repair_wrong_auto_links(
        db,
        txns=txns,
        invoices=invoices,
        supplier_accounts=supplier_accounts,
        invoice_partner_ids=invoice_partner_ids,
        txn_partner_ids=txn_partner_ids,
    )

    # 所有历史 pair 都要读取：rejected 不参与金额，但必须阻止自动复活。
    # 只有人工 link() 才允许用户明确把 rejected pair 重新启用。
    all_existing_links = (
        db.query(TaxInvoiceLink)
        .filter(TaxInvoiceLink.target_type == TARGET_TYPE)
        .all()
    )
    existing_links = [link for link in all_existing_links if link.match_method != "rejected"]
    allocated_by_txn: dict[int, Decimal] = {}
    allocated_by_invoice: dict[int, Decimal] = {}
    existing_invoice_ids = {link.invoice_id for link in existing_links if link.allocated_amount is None}
    existing_invoice_map = {
        row.id: row
        for row in db.query(TaxInvoice).filter(TaxInvoice.id.in_(existing_invoice_ids)).all()
    } if existing_invoice_ids else {}
    for link in existing_links:
        if not link.confirmed:
            continue
        amount = _link_amount(link, existing_invoice_map.get(link.invoice_id))
        allocated_by_txn[link.target_id] = allocated_by_txn.get(link.target_id, Decimal("0")) + amount
        allocated_by_invoice[link.invoice_id] = allocated_by_invoice.get(link.invoice_id, Decimal("0")) + amount
    txn_amount_by_id = {txn.id: _dec(txn.amount) for txn in txns}
    existing_txn_ids: set[int] = {
        txn_id for txn_id, allocated in allocated_by_txn.items()
        if allocated >= txn_amount_by_id.get(txn_id, Decimal("0")) - TOLERANCE
    }
    existing_invoice_txn: set[tuple[int, int]] = {
        (link.invoice_id, link.target_id) for link in all_existing_links
    }

    matched = 0
    skipped = 0
    details: list[dict] = []

    ambiguous_details: list[dict[str, Any]] = []
    for txn in txns:
        txn_total = _dec(txn.amount)
        txn_remaining = txn_total - allocated_by_txn.get(txn.id, Decimal("0"))
        if txn_remaining <= TOLERANCE:
            skipped += 1
            existing_txn_ids.add(txn.id)
            continue

        candidates: list[TaxInvoice] = []
        for invoice in invoices:
            if (invoice.id, txn.id) in existing_invoice_txn:
                continue
            invoice_total = _invoice_target_amount(db, invoice)
            invoice_remaining = invoice_total - allocated_by_invoice.get(invoice.id, Decimal("0"))
            if invoice_remaining <= TOLERANCE or abs(invoice_remaining - txn_remaining) > TOLERANCE:
                continue
            evidence = _candidate_evidence(
                txn,
                invoice,
                supplier_accounts,
                invoice_partner_ids,
                txn_partner_ids,
            )
            if evidence["nameMatch"] or evidence["accountMatch"] or evidence["partnerMatch"]:
                candidates.append(invoice)

        invoice, evidence = _choose_unique_best_candidate(
            txn,
            candidates,
            supplier_accounts,
            invoice_partner_ids,
            txn_partner_ids,
        )
        if invoice is None:
            if evidence.get("ambiguous"):
                skipped += 1
                ambiguous_details.append({
                    "txnId": txn.id,
                    "txnDate": txn.txn_date.isoformat(),
                    "amount": str(txn_remaining),
                    "candidateCount": evidence.get("candidateCount", 0),
                    "candidateInvoiceIds": evidence.get("candidateInvoiceIds", []),
                    "reason": "同证据等级且日期距离相同，未自动匹配",
                })
            continue

        invoice_total = _invoice_target_amount(db, invoice)
        invoice_remaining = invoice_total - allocated_by_invoice.get(invoice.id, Decimal("0"))
        portion = min(invoice_remaining, txn_remaining)
        account_match = bool(evidence.get("accountMatch"))
        partner_match = bool(evidence.get("partnerMatch"))
        method = "business_partner_date" if partner_match else "supplier_account_date" if account_match else "auto_date"
        link_row = TaxInvoiceLink(
            invoice_id=invoice.id,
            target_type=TARGET_TYPE,
            target_id=txn.id,
            allocated_amount=portion,
            match_method=method,
            confidence=Decimal("1.00") if partner_match else Decimal("0.99") if account_match else Decimal("0.95"),
            confirmed=True,
            note=(
                f"系统唯一最佳候选自动匹配：候选{evidence.get('candidateCount', 0)}张；"
                f"统一往来单位={'是' if partner_match else '否'}；"
                f"账号证据={'是' if account_match else '否'}；"
                f"付款/开票日期差{evidence.get('dateDistanceDays')}天"
            ),
        )
        db.add(link_row)
        db.flush()
        allocated_by_txn[txn.id] = allocated_by_txn.get(txn.id, Decimal("0")) + portion
        allocated_by_invoice[invoice.id] = allocated_by_invoice.get(invoice.id, Decimal("0")) + portion
        matched += 1
        if txn_total - allocated_by_txn[txn.id] <= TOLERANCE:
            existing_txn_ids.add(txn.id)
        existing_invoice_txn.add((invoice.id, txn.id))
        details.append({
            "invoiceId": invoice.id,
            "invoiceNumber": invoice.invoice_number,
            "sellerName": invoice.seller_name,
            "txnId": txn.id,
            "txnDate": txn.txn_date.isoformat(),
            "amount": str(portion),
            "matchMethod": method,
            "accountMatched": account_match,
            "partnerMatched": partner_match,
            "partnerId": evidence.get("partnerId"),
            "dateDistanceDays": evidence.get("dateDistanceDays"),
            "candidateCount": evidence.get("candidateCount"),
        })

    # 第二轮：拆分匹配 — 把未配的发票按"同名 + 多笔流水合计 == 发票金额"找出来
    split_matched = 0
    split_details: list[dict] = []
    # 银行付款维度按“剩余未分摊金额”判断，不依赖跨业务共用的 match_status。
    leftover_invoices = [
        inv for inv in invoices
        if _invoice_target_amount(db, inv) - allocated_by_invoice.get(inv.id, Decimal("0")) > TOLERANCE
    ]
    # 仍未配的流水
    leftover_txns = [t for t in txns if t.id not in existing_txn_ids]
    # 按规范化名分组
    inv_groups: dict[str, list[TaxInvoice]] = {}
    for inv in leftover_invoices:
        norm = _normalize_name(inv.seller_name)
        if norm:
            inv_groups.setdefault(norm, []).append(inv)
    txn_groups: dict[str, list[BankTransaction]] = {}
    for txn in leftover_txns:
        norm = _normalize_name(txn.counterparty_name)
        if norm:
            txn_groups.setdefault(norm, []).append(txn)
    for norm, inv_list in inv_groups.items():
        if norm not in txn_groups:
            continue
        txn_list = sorted(txn_groups[norm], key=lambda x: x.txn_date)
        for inv in inv_list:
            inv_total = _invoice_target_amount(db, inv)
            inv_remaining = inv_total - allocated_by_invoice.get(inv.id, Decimal("0"))
            if inv_remaining <= TOLERANCE:
                continue
            # 同名流水按“可用剩余额”累计；历史已部分分配的发票只补齐剩余部分。
            running = Decimal("0.0000")
            needed_txns = []
            for t in txn_list:
                txn_remaining = _dec(t.amount) - allocated_by_txn.get(t.id, Decimal("0"))
                if txn_remaining <= TOLERANCE:
                    continue
                if running >= inv_remaining - TOLERANCE:
                    break
                needed_txns.append(t)
                running += txn_remaining
            # 自动拆分必须真的是“多笔流水 → 一张票”，不能绕过一对一候选唯一规则。
            same_amount_invoice_count = sum(
                1
                for other in inv_list
                if abs(
                    (_invoice_target_amount(db, other) - allocated_by_invoice.get(other.id, Decimal("0")))
                    - inv_remaining
                ) <= TOLERANCE
            )
            if (
                abs(running - inv_remaining) <= TOLERANCE
                and len(needed_txns) >= 2
                and same_amount_invoice_count == 1
            ):
                cnt, allocated = _split_match_invoice_to_txns(
                    db, inv, needed_txns, actor,
                    allocated_by_invoice=allocated_by_invoice,
                    allocated_by_txn=allocated_by_txn,
                )
                if cnt > 0:
                    split_matched += cnt
                    split_details.append({
                        "invoiceId": inv.id,
                        "invoiceNumber": inv.invoice_number,
                        "invoiceAmount": str(inv_total),
                        "txnIds": [t.id for t in needed_txns],
                        "txnDates": [t.txn_date.isoformat() for t in needed_txns],
                    })
                    for t in needed_txns:
                        if allocated_by_txn.get(t.id, Decimal("0")) >= _dec(t.amount) - TOLERANCE:
                            existing_txn_ids.add(t.id)

    # 第三轮：把仍未配的"大流水"拆给多张同名小发票
    big_txn_split_matched = 0
    still_unmatched_txns = [t for t in leftover_txns if t.id not in existing_txn_ids]
    for txn in still_unmatched_txns:
        norm = _normalize_name(txn.counterparty_name)
        if norm not in inv_groups:
            continue
        candidates = [
            inv for inv in inv_groups[norm]
            if _invoice_target_amount(db, inv) - allocated_by_invoice.get(inv.id, Decimal("0")) > TOLERANCE
        ]
        candidates.sort(
            key=lambda inv: (
                abs(((_invoice_issue_date(inv) or txn.txn_date) - txn.txn_date).days),
                inv.id,
            )
        )
        txn_remaining = _dec(txn.amount) - allocated_by_txn.get(txn.id, Decimal("0"))
        selected_total = sum(
            (
                _invoice_target_amount(db, inv) - allocated_by_invoice.get(inv.id, Decimal("0"))
                for inv in candidates
            ),
            Decimal("0.0000"),
        )
        # 大流水拆多票只在“全部剩余候选就是唯一整组”时自动处理，不猜子集。
        if len(candidates) >= 2 and abs(selected_total - txn_remaining) <= TOLERANCE:
            cnt, allocated = _split_match_txn_to_invoices(
                db, txn, candidates, actor,
                allocated_by_invoice=allocated_by_invoice,
                allocated_by_txn=allocated_by_txn,
            )
            if cnt > 0:
                big_txn_split_matched += cnt
                split_details.append({
                    "txnId": txn.id,
                    "txnAmount": str(_dec(txn.amount)),
                    "invoiceIds": [candidate.id for candidate in candidates[:cnt]],
                })

    # 第四轮：账号强证据兜底（例如银行户名与供应商档案名不完全一致）。
    # 仍要求“唯一最佳候选”，避免账号相同/同额票时再次发生抢占。
    supplier_matched = 0
    supplier_details: list[dict] = []
    still_unmatched_after_split = [t for t in leftover_txns if t.id not in existing_txn_ids]
    for txn in still_unmatched_after_split:
        txn_remaining = _dec(txn.amount) - allocated_by_txn.get(txn.id, Decimal("0"))
        if txn_remaining <= TOLERANCE:
            continue
        account = _normalize_account(getattr(txn, "counterparty_account", ""))
        if not account:
            continue
        candidates = []
        for inv in leftover_invoices:
            if (inv.id, txn.id) in existing_invoice_txn:
                continue
            evidence = _candidate_evidence(
                txn,
                inv,
                supplier_accounts,
                invoice_partner_ids,
                txn_partner_ids,
            )
            if not evidence["accountMatch"]:
                continue
            inv_remaining = _invoice_target_amount(db, inv) - allocated_by_invoice.get(inv.id, Decimal("0"))
            if inv_remaining > TOLERANCE and abs(inv_remaining - txn_remaining) <= TOLERANCE:
                candidates.append(inv)
        inv, evidence = _choose_unique_best_candidate(
            txn,
            candidates,
            supplier_accounts,
            invoice_partner_ids,
            txn_partner_ids,
        )
        if inv is None or not evidence.get("accountMatch"):
            if evidence.get("ambiguous"):
                ambiguous_details.append({
                    "txnId": txn.id,
                    "txnDate": txn.txn_date.isoformat(),
                    "amount": str(txn_remaining),
                    "candidateCount": evidence.get("candidateCount", 0),
                    "candidateInvoiceIds": evidence.get("candidateInvoiceIds", []),
                    "reason": "供应商账号一致但最佳候选并列，未自动匹配",
                })
            continue
        portion = txn_remaining
        link_row = TaxInvoiceLink(
            invoice_id=inv.id,
            target_type=TARGET_TYPE,
            target_id=txn.id,
            allocated_amount=portion,
            match_method="supplier_account_date",
            confidence=Decimal("0.99"),
            confirmed=True,
            note=(
                f"供应商账号强证据 + 唯一最近日期自动匹配；"
                f"账号 {account}；日期差{evidence.get('dateDistanceDays')}天"
            ),
        )
        db.add(link_row)
        db.flush()
        allocated_by_invoice[inv.id] = allocated_by_invoice.get(inv.id, Decimal("0")) + portion
        allocated_by_txn[txn.id] = allocated_by_txn.get(txn.id, Decimal("0")) + portion
        existing_invoice_txn.add((inv.id, txn.id))
        existing_txn_ids.add(txn.id)
        supplier_matched += 1
        supplier_details.append({
            "invoiceId": inv.id,
            "invoiceNumber": inv.invoice_number,
            "supplierName": inv.seller_name,
            "bankAccount": account,
            "txnId": txn.id,
            "amount": str(portion),
            "dateDistanceDays": evidence.get("dateDistanceDays"),
            "candidateCount": evidence.get("candidateCount"),
        })

    db.commit()
    audit(
        db, actor, "payment_invoice_match.auto", "tax_invoice_links", "",
        {"year": year, "month": month, "matched": matched, "skipped": skipped,
         "splitMatched": split_matched, "bigTxnSplitMatched": big_txn_split_matched,
         "supplierMatched": supplier_matched, "repaired": len(repair_details),
         "ambiguous": len(ambiguous_details), "repairDetails": repair_details},
    )
    return {
        "year": year, "month": month,
        "matched": matched, "skipped": skipped,
        "splitMatched": split_matched, "bigTxnSplitMatched": big_txn_split_matched,
        "supplierMatched": supplier_matched,
        "repaired": len(repair_details), "ambiguous": len(ambiguous_details),
        "details": details, "splitDetails": split_details, "supplierDetails": supplier_details,
        "repairDetails": repair_details, "ambiguousDetails": ambiguous_details,
    }


def link(
    db: Session,
    *,
    txn_id: int,
    invoice_id: int,
    allocated_amount: Decimal | str | None = None,
    note: str = "",
    actor: str = "system",
) -> dict[str, Any]:
    """手工标记：把一笔银行付款挂到一张进项发票（存在即复活软删行）。"""
    txn = db.get(BankTransaction, txn_id)
    if txn is None:
        raise ValueError("银行流水不存在")
    if txn.direction != "out":
        raise ValueError("只能对支出流水标记发票")
    invoice = db.get(TaxInvoice, invoice_id)
    if invoice is None:
        raise ValueError("发票不存在")
    if invoice.direction != "input":
        raise ValueError("只能挂进项发票")
    if not tax_invoice_service.is_bank_payment_reconciliation_eligible(invoice, db=db):
        raise ValueError(tax_invoice_service.bank_payment_reconciliation_ineligible_reason(invoice, db=db))

    db.refresh(txn, with_for_update=True)
    db.refresh(invoice, with_for_update=True)
    link = (
        db.query(TaxInvoiceLink)
        .filter_by(invoice_id=invoice.id, target_type=TARGET_TYPE, target_id=txn.id)
        .first()
    )
    exclude_id = link.id if link is not None else None
    invoice_total = _invoice_target_amount(db, invoice)
    txn_total = _dec(txn.amount)
    invoice_used = _invoice_bank_allocated(db, invoice.id, exclude_link_id=exclude_id)
    txn_used = _txn_bank_allocated(db, txn.id, exclude_link_id=exclude_id)
    if allocated_amount is None:
        amount = min(invoice_total - invoice_used, txn_total - txn_used)
    else:
        amount = _dec(to_decimal(allocated_amount))

    if amount <= TOLERANCE:
        raise ValueError("分摊金额必须大于 0")
    if invoice_used + amount > invoice_total + TOLERANCE:
        raise ValueError(f"该发票银行付款累计分摊 {invoice_used + amount} 超过票面金额 {invoice_total}")
    if txn_used + amount > txn_total + TOLERANCE:
        raise ValueError(f"该笔付款累计分摊 {txn_used + amount} 超过付款金额 {txn_total}")

    if link is None:
        link = TaxInvoiceLink(invoice_id=invoice.id, target_type=TARGET_TYPE, target_id=txn.id)
        db.add(link)
    link.allocated_amount = amount
    link.match_method = "manual"
    link.confidence = Decimal("1")
    link.confirmed = True
    link.note = note or "付款发票匹配清单手工标记"

    db.commit()
    audit(
        db, actor, "payment_invoice_match.link", "tax_invoice_links", str(link.id),
        {"txnId": txn.id, "invoiceId": invoice.id, "allocatedAmount": str(amount)},
    )
    return {"ok": True, "id": link.id, "invoiceId": invoice.id, "txnId": txn.id}


def unlink(db: Session, link_id: int, actor: str = "system") -> dict[str, Any]:
    """解除标记（软删 match_method=rejected，保审计；同对可再次标记=复活）。"""
    row = db.get(TaxInvoiceLink, link_id)
    if row is None or row.target_type != TARGET_TYPE or row.match_method == "rejected":
        raise ValueError("匹配不存在或已解除")
    row.match_method = "rejected"
    row.confirmed = False
    row.confidence = None
    row.note = "解除银行付款匹配"

    db.commit()
    audit(
        db, actor, "payment_invoice_match.unlink", "tax_invoice_links", str(row.id),
        {"invoiceId": row.invoice_id, "targetId": row.target_id},
    )
    return {"ok": True}
