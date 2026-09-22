"""付款↔发票匹配标记清单（财务月度资料）服务层测试。

覆盖：overview 按月/方向过滤、matched/partial/unmatched 银行状态推导、
金额封顶、发票池统计、同名同金额建议、link 落库但不污染业务匹配状态、
软删复活、方向校验、unlink 与审计日志。
"""
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.bank import BankTransaction
from app.models.business_partner import BusinessPartner, BusinessPartnerIdentifier, BusinessPartnerLink
from app.models.org import AuditLog
from app.models.purchase import Supplier
from app.models.tax import TaxInvoice, TaxInvoiceLink
from app.services import payment_invoice_match_service as pm


def _txn(
    db_session,
    *,
    year=2026,
    month=8,
    day=5,
    direction="out",
    amount="1000.00",
    name="供货商甲",
    summary="货款",
    voucher="",
    counterparty_account="",
) -> BankTransaction:
    row = BankTransaction(
        txn_date=date(year, month, day),
        direction=direction,
        amount=Decimal(amount),
        counterparty_name=name,
        counterparty_account=counterparty_account,
        summary=summary,
        voucher_no=voucher,
        fingerprint=f"pytest-pim|{uuid4().hex}",
        raw={},
    )
    db_session.add(row)
    db_session.flush()
    return row


def _invoice(
    db_session,
    *,
    seller="供货商甲",
    amount="1000.00",
    year=2026,
    month=8,
    day=10,
    direction="input",
) -> TaxInvoice:
    row = TaxInvoice(
        invoice_key=f"pytest-pim|{uuid4().hex}",
        invoice_number=f"PIM-{uuid4().hex[:12]}",
        direction=direction,
        status="issued",
        issue_date=datetime(year, month, day, tzinfo=timezone.utc),
        seller_name=seller,
        total_amount=Decimal(amount),
        source_system="tax_export",
        raw={},
    )
    db_session.add(row)
    db_session.flush()
    return row


# ---------- 纯函数 / 过滤 ----------

def test_month_range_rejects_invalid_month():
    with pytest.raises(ValueError):
        pm._month_range(2026, 0)
    with pytest.raises(ValueError):
        pm._month_range(2026, 13)
    assert pm._month_range(2026, 2) == (date(2026, 2, 1), date(2026, 2, 28))


def test_overview_filters_by_month_and_out_direction(db_session):
    in_month = _txn(db_session, amount="500.00")
    _txn(db_session, month=7, amount="900.00")  # 跨月不进清单
    _txn(db_session, direction="in", amount="700.00")  # 收款不进清单
    _invoice(db_session, amount="500.00")

    data = pm.overview(db_session, 2026, 8)
    assert [row["id"] for row in data["payments"]] == [in_month.id]
    assert data["summary"]["paymentTotal"] == "500.00"
    assert data["summary"]["txnCount"] == 1
    assert data["summary"]["unmatchedCount"] == 1


def test_status_never_calls_zero_allocation_matched():
    assert pm._status(Decimal("0.01"), Decimal("0.01")) == "unmatched"
    assert pm._status(Decimal("100.00"), Decimal("100.00")) == "unmatched"
    assert pm._status(Decimal("0.00"), Decimal("100.00")) == "matched"
    assert pm._status(Decimal("40.00"), Decimal("100.00")) == "partial"


def test_overview_status_matched_after_link(db_session):
    txn = _txn(db_session, amount="1000.00")
    inv = _invoice(db_session, amount="1000.00")
    pm.link(db_session, txn_id=txn.id, invoice_id=inv.id)

    data = pm.overview(db_session, 2026, 8)
    row = data["payments"][0]
    assert row["status"] == "matched"
    assert row["matchedAmount"] == "1000.00"
    assert row["remaining"] == "0.00"
    assert row["invoices"][0]["invoiceId"] == inv.id
    assert data["summary"]["matchedCount"] == 1
    assert data["summary"]["matchedTotal"] == "1000.00"


def test_overview_status_partial(db_session):
    txn = _txn(db_session, amount="1000.00")
    inv = _invoice(db_session, amount="600.00")
    pm.link(db_session, txn_id=txn.id, invoice_id=inv.id)

    row = pm.overview(db_session, 2026, 8)["payments"][0]
    assert row["status"] == "partial"
    assert row["matchedAmount"] == "600.00"
    assert row["remaining"] == "400.00"


def test_overview_status_unmatched_no_links(db_session):
    _txn(db_session, amount="300.00")
    data = pm.overview(db_session, 2026, 8)
    row = data["payments"][0]
    assert row["status"] == "unmatched"
    assert row["invoices"] == []
    assert data["summary"]["unmatchedTotal"] == "300.00"


def test_overview_matched_amount_capped_at_txn_amount(db_session):
    txn = _txn(db_session, amount="100.00")
    inv_a = _invoice(db_session, seller="供货商甲", amount="80.00")
    inv_b = _invoice(db_session, seller="供货商乙", amount="80.00")
    pm.link(db_session, txn_id=txn.id, invoice_id=inv_a.id)
    pm.link(db_session, txn_id=txn.id, invoice_id=inv_b.id)

    row = pm.overview(db_session, 2026, 8)["payments"][0]
    assert len(row["invoices"]) == 2
    assert row["matchedAmount"] == "100.00"  # 封顶不超付款金额
    assert row["remaining"] == "0.00"
    assert row["status"] == "matched"


def test_overview_invoice_pool_counts_bank_links_only(db_session):
    txn = _txn(db_session, amount="400.00")
    linked = _invoice(db_session, amount="400.00")
    other_month = _invoice(db_session, amount="100.00", month=7)
    output = _invoice(db_session, amount="200.00", direction="output")
    pm.link(db_session, txn_id=txn.id, invoice_id=linked.id)

    data = pm.overview(db_session, 2026, 8)
    pool_ids = {row["id"] for row in data["invoicePool"]}
    assert linked.id in pool_ids
    assert other_month.id not in pool_ids
    assert output.id not in pool_ids
    row = next(r for r in data["invoicePool"] if r["id"] == linked.id)
    assert row["bankLinkedAmount"] == "400.00"
    assert row["remaining"] == "0.00"
    assert row["bankMatchStatus"] == "matched"
    assert row["links"][0]["counterpartyName"] == "供货商甲"
    assert row["links"][0]["txnId"] == txn.id
    assert data["summary"]["invoiceTotal"] == "400.00"
    assert data["summary"]["invoiceMatchedTotal"] == "400.00"
    assert data["summary"]["invoiceOutstandingTotal"] == "0.00"
    assert data["summary"]["invoiceMatchedCount"] == 1




def test_red_or_non_positive_invoice_never_enters_bank_reconciliation(db_session):
    """负数/红冲票属于会计事实，不属于银行付款待核对项。"""
    txn = _txn(db_session, amount="1518.00", name="龙港市丽峰包装有限公司")
    inv = _invoice(
        db_session,
        seller="龙港市丽峰包装有限公司",
        amount="-1518.00",
        month=7,
        day=8,
    )
    txn.txn_date = date(2026, 7, 9)
    inv.status = "red"
    db_session.commit()

    data = pm.overview(db_session, 2026, 7)
    assert inv.id not in {row["id"] for row in data["invoicePool"]}
    assert data["summary"]["invoiceCount"] == 0
    assert data["summary"]["invoiceTotal"] == "0.00"
    assert data["summary"]["invoiceMatchedCount"] == 0

    with pytest.raises(ValueError, match="不参与银行付款核对"):
        pm.link(db_session, txn_id=txn.id, invoice_id=inv.id)

    auto = pm.auto_match(db_session, 2026, 7)
    assert auto["matched"] == 0
    assert db_session.query(TaxInvoiceLink).filter_by(
        invoice_id=inv.id, target_type="bank_transaction"
    ).count() == 0


def test_positive_red_invoice_also_stays_out_of_bank_reconciliation(db_session):
    """已红冲的正数蓝字票也不能重新进入付款核对。"""
    txn = _txn(db_session, amount="500.00", name="已红冲供应商")
    inv = _invoice(db_session, seller="已红冲供应商", amount="500.00")
    inv.status = "red"
    db_session.commit()

    data = pm.overview(db_session, 2026, 8)
    assert inv.id not in {row["id"] for row in data["invoicePool"]}
    with pytest.raises(ValueError, match="红冲相关发票不参与银行付款核对"):
        pm.link(db_session, txn_id=txn.id, invoice_id=inv.id)


def test_txn_reconciliation_statuses_require_confirmed_amount_coverage(db_session):
    txn = _txn(db_session, amount="1000.00")
    inv = _invoice(db_session, amount="1000.00")
    link = TaxInvoiceLink(
        invoice_id=inv.id,
        target_type="bank_transaction",
        target_id=txn.id,
        allocated_amount=Decimal("1000.00"),
        match_method="manual",
        confirmed=False,
    )
    db_session.add(link)
    db_session.flush()

    status = pm.txn_reconciliation_statuses(db_session, [txn.id])[txn.id]
    assert status["status"] == "unmatched"
    assert status["allocatedAmount"] == "0.00"
    assert status["remainingAmount"] == "1000.00"

    link.confirmed = True
    link.allocated_amount = Decimal("400.00")
    db_session.flush()
    status = pm.txn_reconciliation_statuses(db_session, [txn.id])[txn.id]
    assert status["status"] == "partial"
    assert status["allocatedAmount"] == "400.00"
    assert status["remainingAmount"] == "600.00"

    link.allocated_amount = Decimal("1000.00")
    db_session.flush()
    status = pm.txn_reconciliation_statuses(db_session, [txn.id])[txn.id]
    assert status["status"] == "matched"
    assert status["allocatedAmount"] == "1000.00"
    assert status["remainingAmount"] == "0.00"


def test_pending_invoice_pool_ignores_business_match_status_and_uses_bank_remaining(db_session):
    txn = _txn(db_session, amount="400.00", month=7)
    inv = _invoice(db_session, amount="1000.00", month=6)
    inv.match_status = "matched"  # 已和采购单配平，也仍然需要独立做银行付款核对。
    db_session.flush()

    pm.link(db_session, txn_id=txn.id, invoice_id=inv.id, allocated_amount="400.00")

    rows = pm.pending_invoices(db_session)
    row = next(item for item in rows if item["id"] == inv.id)
    assert row["bankLinkedAmount"] == "400.00"
    assert row["remaining"] == "600.00"
    assert row["bankMatchStatus"] == "partial"

    db_session.refresh(inv)
    assert inv.match_status == "matched"


def test_suggestion_same_name_and_equal_remaining(db_session):
    txn = _txn(db_session, amount="500.00", name="杭州纸箱厂")
    hit = _invoice(db_session, seller="杭州纸箱厂", amount="500.00")
    miss = _invoice(db_session, seller="别的公司", amount="500.00")

    data = pm.overview(db_session, 2026, 8)
    assert hit.id in data["payments"][0]["suggestedInvoiceIds"]
    assert miss.id not in data["payments"][0]["suggestedInvoiceIds"]
    row = next(r for r in data["invoicePool"] if r["id"] == hit.id)
    assert row["suggested"] is True
    assert row["suggestedPaymentIds"] == [txn.id]


# ---------- link / unlink ----------

def test_bank_link_does_not_mutate_business_match_status_or_note(db_session):
    txn = _txn(db_session, amount="1000.00")
    inv = _invoice(db_session, amount="1000.00")

    result = pm.link(db_session, txn_id=txn.id, invoice_id=inv.id, actor="pytest-admin")
    assert result["ok"] is True

    row = db_session.get(TaxInvoiceLink, result["id"])
    assert row.match_method == "manual"
    assert row.confirmed is True
    assert row.allocated_amount == Decimal("1000.0000")

    db_session.refresh(inv)
    assert inv.match_status == "unmatched"
    assert inv.match_note == ""

    log = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "payment_invoice_match.link")
    )
    assert log is not None


def test_link_revives_rejected_link_and_validates_direction(db_session):
    txn = _txn(db_session, amount="1000.00")
    inv = _invoice(db_session, amount="1000.00")

    first = pm.link(db_session, txn_id=txn.id, invoice_id=inv.id)
    link_row = db_session.get(TaxInvoiceLink, first["id"])
    pm.unlink(db_session, link_row.id)
    assert link_row.match_method == "rejected"

    again = pm.link(db_session, txn_id=txn.id, invoice_id=inv.id)
    assert again["id"] == first["id"]  # 同对复活软删行，不新开记录

    income = _txn(db_session, direction="in")
    with pytest.raises(ValueError, match="支出"):
        pm.link(db_session, txn_id=income.id, invoice_id=inv.id)

    output_inv = _invoice(db_session, direction="output")
    with pytest.raises(ValueError, match="进项"):
        pm.link(db_session, txn_id=txn.id, invoice_id=output_inv.id)


def test_unlink_keeps_business_status_untouched_and_rejects_double_unlink(db_session):
    txn = _txn(db_session, amount="1000.00")
    inv = _invoice(db_session, amount="1000.00")
    first = pm.link(db_session, txn_id=txn.id, invoice_id=inv.id)
    link_row = db_session.get(TaxInvoiceLink, first["id"])

    assert pm.unlink(db_session, link_row.id, actor="pytest-admin")["ok"] is True
    db_session.refresh(inv)
    assert inv.match_status == "unmatched"
    assert link_row.match_method == "rejected"
    assert link_row.confirmed is False

    log = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "payment_invoice_match.unlink")
    )
    assert log is not None

    with pytest.raises(ValueError, match="已解除"):
        pm.unlink(db_session, link_row.id)



def test_legacy_bank_link_without_allocated_amount_uses_same_full_amount_everywhere(db_session):
    txn = _txn(db_session, amount="1000.00")
    inv = _invoice(db_session, amount="1000.00")
    db_session.add(TaxInvoiceLink(
        invoice_id=inv.id,
        target_type="bank_transaction",
        target_id=txn.id,
        allocated_amount=None,
        match_method="manual",
        confirmed=True,
    ))
    db_session.commit()

    status = pm.txn_reconciliation_statuses(db_session, [txn.id])[txn.id]
    assert status["status"] == "matched"
    assert status["allocatedAmount"] == "1000.00"
    assert status["remainingAmount"] == "0.00"

    pending_ids = {row["id"] for row in pm.pending_invoices(db_session)}
    assert inv.id not in pending_ids

    # 同一 invoice/txn 的历史 NULL 分摊链接再次确认时，应该复用原 link 并规范化为明确金额，
    # 不能新建重复链接，也不能错误提示“无剩余额度”。
    normalized = pm.link(db_session, txn_id=txn.id, invoice_id=inv.id)
    legacy_link = db_session.get(TaxInvoiceLink, normalized["id"])
    assert legacy_link is not None
    assert legacy_link.allocated_amount == Decimal("1000.0000")
    assert db_session.query(TaxInvoiceLink).filter_by(
        invoice_id=inv.id,
        target_type="bank_transaction",
        target_id=txn.id,
    ).count() == 1

    # 但这笔付款已被完整占用，不能再分摊给另一张发票。
    other = _invoice(db_session, seller="另一供应商", amount="100.00")
    with pytest.raises(ValueError, match="分摊金额必须大于 0"):
        pm.link(db_session, txn_id=txn.id, invoice_id=other.id)



def test_transaction_api_legacy_matched_follows_direction_specific_domain(client, db_session):
    """旧 matched 字段也必须按方向映射，不能让已核对发票的支出继续显示未匹配。"""
    txn = _txn(db_session, amount="321.00", name="兼容字段供应商")
    inv = _invoice(db_session, seller="兼容字段供应商", amount="321.00")
    pm.link(db_session, txn_id=txn.id, invoice_id=inv.id)
    db_session.commit()

    response = client.get(
        "/api/v1/reconciliation/transactions",
        params={
            "direction": "out",
            "start_date": "2026-08-05",
            "end_date": "2026-08-05",
            "q": "兼容字段供应商",
        },
    )
    assert response.status_code == 200
    row = next(item for item in response.json() if item["id"] == txn.id)
    assert row["matched"] is True
    assert row["settlementMatched"] is False
    assert row["settlementMatchStatus"] == "not_applicable"
    assert row["invoicePaymentMatched"] is True
    assert row["invoicePaymentMatchStatus"] == "matched"
    assert row["matchStatus"] == "matched"
    assert row["settlementMatchedAt"] is None
    assert row["invoicePaymentMatchedAt"]
    assert row["matchedAt"] == row["invoicePaymentMatchedAt"]



def test_auto_match_only_mutates_selected_month_payments(db_session):
    """8 月自动匹配不能顺手修改 7 月银行流水。"""
    july_txn = _txn(
        db_session, year=2026, month=7, day=20,
        amount="500.00", name="跨月边界供应商",
    )
    inv = _invoice(
        db_session, year=2026, month=8, day=5,
        amount="500.00", seller="跨月边界供应商",
    )
    db_session.commit()

    result = pm.auto_match(db_session, 2026, 8)
    assert result["matched"] == 0
    assert db_session.query(TaxInvoiceLink).filter_by(
        invoice_id=inv.id,
        target_type="bank_transaction",
        target_id=july_txn.id,
    ).count() == 0


def test_auto_match_respects_rejected_pair(db_session):
    """人工拒绝过的 pair 不得被自动匹配复活，也不能撞唯一约束。"""
    txn = _txn(db_session, amount="888.00", name="拒绝测试供应商")
    inv = _invoice(db_session, amount="888.00", seller="拒绝测试供应商")
    rejected = TaxInvoiceLink(
        invoice_id=inv.id,
        target_type="bank_transaction",
        target_id=txn.id,
        allocated_amount=Decimal("888.00"),
        match_method="rejected",
        confirmed=False,
    )
    db_session.add(rejected)
    db_session.commit()

    result = pm.auto_match(db_session, 2026, 8)
    assert result["matched"] == 0
    rows = db_session.query(TaxInvoiceLink).filter_by(
        invoice_id=inv.id,
        target_type="bank_transaction",
        target_id=txn.id,
    ).all()
    assert len(rows) == 1
    assert rows[0].match_method == "rejected"
    assert rows[0].confirmed is False


def test_auto_split_does_not_partially_allocate_non_exact_same_name_group(db_session):
    """同名只能作为候选；组合金额不精确相等时不能自动切一部分付款。"""
    txn = _txn(db_session, amount="5000.00", name="拆分安全供应商")
    inv_a = _invoice(db_session, amount="1000.00", seller="拆分安全供应商", day=8)
    inv_b = _invoice(db_session, amount="1000.00", seller="拆分安全供应商", day=9)
    db_session.commit()

    result = pm.auto_match(db_session, 2026, 8)
    assert result["matched"] == 0
    assert result["splitMatched"] == 0
    assert result["bigTxnSplitMatched"] == 0
    assert db_session.query(TaxInvoiceLink).filter(
        TaxInvoiceLink.invoice_id.in_([inv_a.id, inv_b.id]),
        TaxInvoiceLink.target_type == "bank_transaction",
    ).count() == 0
    status = pm.txn_reconciliation_statuses(db_session, [txn.id])[txn.id]
    assert status["status"] == "unmatched"


def test_auto_split_allows_exact_multi_invoice_total(db_session):
    """一笔付款拆多票时，只有多票剩余金额合计精确等于付款剩余金额才自动落库。"""
    txn = _txn(db_session, amount="5000.00", name="精确拆分供应商")
    inv_a = _invoice(db_session, amount="3000.00", seller="精确拆分供应商", day=8)
    inv_b = _invoice(db_session, amount="2000.00", seller="精确拆分供应商", day=9)
    db_session.commit()

    result = pm.auto_match(db_session, 2026, 8)
    assert result["bigTxnSplitMatched"] == 2
    links = db_session.query(TaxInvoiceLink).filter(
        TaxInvoiceLink.invoice_id.in_([inv_a.id, inv_b.id]),
        TaxInvoiceLink.target_type == "bank_transaction",
        TaxInvoiceLink.match_method != "rejected",
    ).all()
    assert len(links) == 2
    assert sum((row.allocated_amount for row in links), Decimal("0")) == Decimal("5000.0000")
    status = pm.txn_reconciliation_statuses(db_session, [txn.id])[txn.id]
    assert status["status"] == "matched"



def _add_supplier_bank_account(db_session, *, name: str, account: str) -> Supplier:
    token = uuid4().hex[:10]
    supplier = Supplier(
        platform="other",
        external_shop_id=f"PIM-SUP-{token}",
        name=name,
        bank_account_no=account,
        bank_account_name=name,
    )
    db_session.add(supplier)
    db_session.flush()
    return supplier


def test_auto_match_prefers_unique_nearest_invoice_with_supplier_account(db_session):
    """真实故障回归：同供应商同金额多票时，账号证据后必须选日期唯一最近的票。"""
    supplier = "合锦供应链"
    account = "6222-8800-3080"
    _add_supplier_bank_account(db_session, name=supplier, account=account)
    old_invoice = _invoice(
        db_session, seller=supplier, amount="3080.00", year=2026, month=4, day=21,
    )
    new_invoice = _invoice(
        db_session, seller=supplier, amount="3080.00", year=2026, month=7, day=24,
    )
    txn = _txn(
        db_session,
        year=2026, month=7, day=22,
        amount="3080.00", name=supplier,
        voucher="9981871",
        counterparty_account="622288003080",
    )
    db_session.commit()

    result = pm.auto_match(db_session, 2026, 7)

    assert result["matched"] == 1
    assert result["ambiguous"] == 0
    active = db_session.query(TaxInvoiceLink).filter(
        TaxInvoiceLink.target_type == "bank_transaction",
        TaxInvoiceLink.target_id == txn.id,
        TaxInvoiceLink.confirmed.is_(True),
        TaxInvoiceLink.match_method != "rejected",
    ).all()
    assert len(active) == 1
    assert active[0].invoice_id == new_invoice.id
    assert active[0].match_method == "supplier_account_date"
    assert active[0].allocated_amount == Decimal("3080.0000")
    assert not any(row.invoice_id == old_invoice.id for row in active)
    assert result["details"][0]["dateDistanceDays"] == 2
    assert result["details"][0]["candidateCount"] == 2
    assert result["details"][0]["accountMatched"] is True


def test_auto_match_repairs_wrong_historical_auto_link_to_unique_nearest_invoice(db_session):
    """已被旧规则错占的整笔 auto 关联允许纠偏；旧关系软拒绝保留审计。"""
    supplier = "合锦供应链"
    account = "622288003080"
    _add_supplier_bank_account(db_session, name=supplier, account=account)
    old_invoice = _invoice(
        db_session, seller=supplier, amount="3080.00", year=2026, month=4, day=21,
    )
    new_invoice = _invoice(
        db_session, seller=supplier, amount="3080.00", year=2026, month=7, day=24,
    )
    txn = _txn(
        db_session,
        year=2026, month=7, day=22,
        amount="3080.00", name=supplier,
        voucher="9981871",
        counterparty_account=account,
    )
    wrong = TaxInvoiceLink(
        invoice_id=old_invoice.id,
        target_type="bank_transaction",
        target_id=txn.id,
        allocated_amount=Decimal("3080.00"),
        match_method="auto",
        confidence=Decimal("1"),
        confirmed=True,
        note="旧规则同名同金额自动匹配",
    )
    db_session.add(wrong)
    db_session.commit()

    result = pm.auto_match(db_session, 2026, 7)

    assert result["repaired"] == 1
    assert result["matched"] == 0
    db_session.refresh(wrong)
    assert wrong.match_method == "rejected"
    assert wrong.confirmed is False
    assert "系统纠偏" in wrong.note

    active = db_session.query(TaxInvoiceLink).filter(
        TaxInvoiceLink.target_type == "bank_transaction",
        TaxInvoiceLink.target_id == txn.id,
        TaxInvoiceLink.confirmed.is_(True),
        TaxInvoiceLink.match_method != "rejected",
    ).all()
    assert len(active) == 1
    assert active[0].invoice_id == new_invoice.id
    assert active[0].match_method == "auto_reassigned"
    assert result["repairDetails"][0]["fromInvoiceId"] == old_invoice.id
    assert result["repairDetails"][0]["toInvoiceId"] == new_invoice.id
    assert result["repairDetails"][0]["dateDistanceDays"] == 2


def test_auto_match_does_not_guess_when_best_candidates_tie(db_session):
    """同额候选日期距离并列时必须留给人工，不能按数据库顺序抢占。"""
    supplier = "并列候选供应商"
    account = "90003080"
    _add_supplier_bank_account(db_session, name=supplier, account=account)
    inv_before = _invoice(
        db_session, seller=supplier, amount="3080.00", year=2026, month=7, day=21,
    )
    inv_after = _invoice(
        db_session, seller=supplier, amount="3080.00", year=2026, month=7, day=23,
    )
    txn = _txn(
        db_session,
        year=2026, month=7, day=22,
        amount="3080.00", name=supplier,
        counterparty_account=account,
    )
    db_session.commit()

    result = pm.auto_match(db_session, 2026, 7)

    assert result["matched"] == 0
    assert result["ambiguous"] >= 1
    assert db_session.query(TaxInvoiceLink).filter(
        TaxInvoiceLink.invoice_id.in_([inv_before.id, inv_after.id]),
        TaxInvoiceLink.target_type == "bank_transaction",
        TaxInvoiceLink.target_id == txn.id,
        TaxInvoiceLink.confirmed.is_(True),
    ).count() == 0


def test_auto_match_never_reassigns_manual_link_even_if_another_invoice_is_closer(db_session):
    """人工确认优先级高于系统日期评分，任何时候都不得被自动纠偏覆盖。"""
    supplier = "人工保护供应商"
    account = "MANUAL3080"
    _add_supplier_bank_account(db_session, name=supplier, account=account)
    old_invoice = _invoice(
        db_session, seller=supplier, amount="3080.00", year=2026, month=4, day=21,
    )
    new_invoice = _invoice(
        db_session, seller=supplier, amount="3080.00", year=2026, month=7, day=24,
    )
    txn = _txn(
        db_session,
        year=2026, month=7, day=22,
        amount="3080.00", name=supplier,
        counterparty_account=account,
    )
    manual = TaxInvoiceLink(
        invoice_id=old_invoice.id,
        target_type="bank_transaction",
        target_id=txn.id,
        allocated_amount=Decimal("3080.00"),
        match_method="manual",
        confidence=Decimal("1"),
        confirmed=True,
        note="财务人工确认",
    )
    db_session.add(manual)
    db_session.commit()

    result = pm.auto_match(db_session, 2026, 7)

    assert result["repaired"] == 0
    db_session.refresh(manual)
    assert manual.match_method == "manual"
    assert manual.confirmed is True
    assert db_session.query(TaxInvoiceLink).filter(
        TaxInvoiceLink.invoice_id == new_invoice.id,
        TaxInvoiceLink.target_type == "bank_transaction",
        TaxInvoiceLink.target_id == txn.id,
        TaxInvoiceLink.confirmed.is_(True),
    ).count() == 0

def test_partial_red_after_full_bank_payment_is_overpaid_not_matched(db_session):
    blue = _invoice(db_session, seller="红冲后超付供应商", amount="1000.00", month=8, day=2)
    txn = _txn(db_session, amount="1000.00", name="红冲后超付供应商", month=8, day=3)
    db_session.flush()
    pm.link(db_session, txn_id=txn.id, invoice_id=blue.id, allocated_amount="1000.00")

    red = TaxInvoice(
        invoice_key=f"pytest-pim-red|{uuid4().hex}",
        invoice_number=f"PIM-RED-{uuid4().hex[:12]}",
        direction="input",
        status="red",
        issue_date=datetime(2026, 8, 10, tzinfo=timezone.utc),
        seller_name=blue.seller_name,
        total_amount=Decimal("-300.00"),
        source_system="tax_export",
        raw={"是否正数发票": "否", "备注": f"被红冲蓝字发票号码：{blue.invoice_number}"},
    )
    db_session.add(red)
    db_session.commit()

    data = pm.overview(db_session, 2026, 8)
    row = next(item for item in data["invoicePool"] if item["id"] == blue.id)
    assert row["totalAmount"] == "700.00"
    assert row["bankLinkedAmount"] == "1000.00"
    assert row["remaining"] == "0.00"
    assert row["overpaidAfterRedAmount"] == "300.00"
    assert row["bankMatchStatus"] == "overpaid_after_red"
    assert data["summary"]["invoiceOverpaidAfterRedCount"] == 1
    assert data["summary"]["invoiceOverpaidAfterRedTotal"] == "300.00"
    assert data["summary"]["invoiceOutstandingTotal"] == "0.00"

    # 供应商退款登记后，历史超额事实仍保留，但当前待处理金额必须归零。
    refund = _txn(
        db_session,
        direction="in",
        amount="300.00",
        name=blue.seller_name,
        day=15,
        summary="红冲退款",
    )
    db_session.commit()
    from app.services import tax_invoice_service

    tax_invoice_service.add_red_settlement(
        db_session,
        red.id,
        tax_invoice_service.RED_BANK_REFUND_TARGET_TYPE,
        Decimal("300.00"),
        target_id=refund.id,
        actor="pytest",
    )

    resolved = pm.overview(db_session, 2026, 8)
    resolved_row = next(item for item in resolved["invoicePool"] if item["id"] == blue.id)
    assert resolved_row["bankMatchStatus"] == "red_overpayment_settled"
    assert resolved_row["overpaidAfterRedAmount"] == "300.00"
    assert resolved_row["overpaidSettledAmount"] == "300.00"
    assert resolved_row["overpaidUnsettledAmount"] == "0.00"
    assert resolved["summary"]["invoiceOverpaidAfterRedCount"] == 0
    assert resolved["summary"]["invoiceResolvedRedOverpaymentCount"] == 1
    assert resolved["summary"]["invoiceOverpaidAfterRedTotal"] == "0.00"
    assert resolved["summary"]["invoiceHistoricalOverpaidAfterRedTotal"] == "300.00"
    assert resolved["summary"]["invoiceSettledOverpaidAfterRedTotal"] == "300.00"


def test_invoice_pool_uses_asia_shanghai_month_boundaries(db_session):
    from zoneinfo import ZoneInfo
    from app.config import settings

    tz = ZoneInfo(settings.TZ)
    august = _invoice(db_session, seller="月末边界供应商", amount="10.00")
    august.issue_date = datetime(2026, 8, 31, 23, 30, tzinfo=tz)
    september = _invoice(db_session, seller="月初边界供应商", amount="20.00", month=9, day=1)
    september.issue_date = datetime(2026, 9, 1, 0, 30, tzinfo=tz)
    db_session.commit()

    august_ids = {row["id"] for row in pm.overview(db_session, 2026, 8)["invoicePool"]}
    september_ids = {row["id"] for row in pm.overview(db_session, 2026, 9)["invoicePool"]}
    assert august.id in august_ids
    assert september.id not in august_ids
    assert september.id in september_ids



def test_auto_match_all_periods_reconciles_historical_bank_months(db_session):
    """“全部来源”总核对必须覆盖历史银行账期，而不是只处理当前月份。"""
    july_txn = _txn(
        db_session, year=2026, month=7, day=22,
        amount="3080.00", name="合锦（广州）供应链有限公司",
    )
    july_inv = _invoice(
        db_session, year=2026, month=7, day=24,
        amount="3080.00", seller="合锦（广州）供应链有限公司",
    )
    april_txn = _txn(
        db_session, year=2026, month=4, day=20,
        amount="2310.00", name="合锦（广州）供应链有限公司",
    )
    april_inv = _invoice(
        db_session, year=2026, month=4, day=21,
        amount="2310.00", seller="合锦（广州）供应链有限公司",
    )
    db_session.commit()

    result = pm.auto_match_all_periods(db_session, actor="pytest")

    assert result["periodCount"] >= 2
    assert result["matchedLinks"] >= 2
    assert {"2026-04", "2026-07"}.issubset(set(result["periods"]))
    links = db_session.query(TaxInvoiceLink).filter(
        TaxInvoiceLink.target_type == "bank_transaction",
        TaxInvoiceLink.confirmed.is_(True),
        TaxInvoiceLink.invoice_id.in_([july_inv.id, april_inv.id]),
    ).all()
    assert {(row.invoice_id, row.target_id) for row in links} == {
        (july_inv.id, july_txn.id),
        (april_inv.id, april_txn.id),
    }



def test_name_normalization_treats_full_and_half_width_parentheses_as_equal():
    assert pm._normalize_name("合锦（广州）供应链有限公司") == pm._normalize_name("合锦(广州)供应链有限公司")


def test_auto_match_uses_confirmed_business_partner_identity_for_hejin_3080(db_session):
    """真实故障回归：统一往来单位已经确认同一主体时，不应再次被原始户名差异挡住。"""
    partner = BusinessPartner(
        name="合锦（广州）供应链有限公司",
        normalized_name="合锦(广州)供应链有限公司",
        tax_no="91440101MA5AQUHB7W",
        roles=["supplier", "counterparty"],
        status="active",
    )
    db_session.add(partner)
    db_session.flush()

    invoice = _invoice(
        db_session,
        seller="合锦（广州）供应链有限公司",
        amount="3080.00",
        year=2026,
        month=7,
        day=24,
    )
    invoice.seller_tax_id = "91440101MA5AQUHB7W"
    txn = _txn(
        db_session,
        year=2026,
        month=7,
        day=22,
        amount="3080.00",
        # 模拟银行侧名称与税票名称并非严格全等；括号本身已由 normalize 兼容。
        name="合锦(广州)供应链结算户",
        counterparty_account="44090001040020264",
    )
    db_session.add_all([
        BusinessPartnerLink(
            partner_id=partner.id,
            source_type="tax_invoice",
            source_id=invoice.id,
            relation_role="seller",
            raw_name=invoice.seller_name,
            raw_tax_no=invoice.seller_tax_id,
            status="linked",
            match_method="tax_no",
            confidence=1.0,
            confirmed=True,
        ),
        BusinessPartnerLink(
            partner_id=partner.id,
            source_type="bank_transaction",
            source_id=txn.id,
            relation_role="counterparty",
            raw_name=txn.counterparty_name,
            raw_account_no=txn.counterparty_account,
            status="linked",
            match_method="bank_account",
            confidence=0.99,
            confirmed=True,
        ),
    ])
    db_session.commit()

    result = pm.auto_match(db_session, 2026, 7)

    assert result["matched"] == 1
    link = db_session.query(TaxInvoiceLink).filter_by(
        invoice_id=invoice.id,
        target_type="bank_transaction",
        target_id=txn.id,
    ).one()
    assert link.confirmed is True
    assert link.match_method == "business_partner_date"
    assert link.allocated_amount == Decimal("3080.0000")
    assert result["details"][0]["partnerMatched"] is True
    assert result["details"][0]["dateDistanceDays"] == 2



def test_multiple_partner_bank_accounts_are_trusted_for_same_tax_number(db_session):
    partner = BusinessPartner(
        name="多银行账号供应商",
        normalized_name="多银行账号供应商",
        tax_no="91330000MULTI001",
        bank_account_no="10000001",
        bank_accounts=[
            {"bank_name": "银行A", "account_no": "10000001", "account_name": "主体", "is_primary": True},
            {"bank_name": "银行B", "account_no": "10000002", "account_name": "主体结算户", "is_primary": False},
        ],
        roles=["supplier"],
        status="active",
    )
    db_session.add(partner)
    db_session.flush()
    db_session.add(
        BusinessPartnerIdentifier(
            partner_id=partner.id,
            kind="bank_account",
            value="10000002",
            normalized_value="10000002",
            source="manual",
        )
    )
    invoice = _invoice(db_session, seller="多银行账号供应商", amount="1888.00", year=2026, month=9, day=12)
    invoice.seller_tax_id = "91330000MULTI001"
    txn = _txn(
        db_session,
        year=2026,
        month=9,
        day=10,
        amount="1888.00",
        name="完全不同的银行户名",
        counterparty_account="10000002",
    )
    db_session.commit()

    result = pm.auto_match(db_session, 2026, 9)

    link = db_session.query(TaxInvoiceLink).filter_by(
        invoice_id=invoice.id,
        target_type="bank_transaction",
        target_id=txn.id,
    ).one()
    assert link.confirmed is True
    assert link.match_method == "supplier_account_date"
    assert result["details"][0]["accountMatched"] is True


def test_repeated_confirmed_tax_account_history_becomes_trusted_identity(db_session):
    tax_no = "91330000LEARN001"
    account = "622299990001"
    for month, amount in [(5, "501.00"), (6, "502.00")]:
        invoice = _invoice(
            db_session,
            seller=f"历史发票名称{month}",
            amount=amount,
            year=2026,
            month=month,
            day=11,
        )
        invoice.seller_tax_id = tax_no
        txn = _txn(
            db_session,
            year=2026,
            month=month,
            day=10,
            amount=amount,
            name=f"历史银行名称{month}",
            counterparty_account=account,
        )
        db_session.add(
            TaxInvoiceLink(
                invoice_id=invoice.id,
                target_type="bank_transaction",
                target_id=txn.id,
                allocated_amount=Decimal(amount),
                match_method="manual",
                confidence=Decimal("1.00"),
                confirmed=True,
            )
        )

    new_invoice = _invoice(
        db_session,
        seller="以后新开的名称",
        amount="3080.00",
        year=2026,
        month=7,
        day=24,
    )
    new_invoice.seller_tax_id = tax_no
    new_txn = _txn(
        db_session,
        year=2026,
        month=7,
        day=22,
        amount="3080.00",
        name="以后新的银行户名",
        counterparty_account=account,
    )
    db_session.commit()

    result = pm.auto_match(db_session, 2026, 7)

    link = db_session.query(TaxInvoiceLink).filter_by(
        invoice_id=new_invoice.id,
        target_type="bank_transaction",
        target_id=new_txn.id,
    ).one()
    assert link.confirmed is True
    assert link.match_method == "supplier_account_date"
    assert result["details"][0]["accountMatched"] is True


def test_single_tax_account_history_is_not_enough_to_learn(db_session):
    tax_no = "91330000ONCE001"
    account = "622299990099"
    old_invoice = _invoice(db_session, seller="旧名称", amount="801.00", year=2026, month=5, day=11)
    old_invoice.seller_tax_id = tax_no
    old_txn = _txn(
        db_session,
        year=2026,
        month=5,
        day=10,
        amount="801.00",
        name="旧银行名",
        counterparty_account=account,
    )
    db_session.add(
        TaxInvoiceLink(
            invoice_id=old_invoice.id,
            target_type="bank_transaction",
            target_id=old_txn.id,
            allocated_amount=Decimal("801.00"),
            match_method="manual",
            confidence=Decimal("1.00"),
            confirmed=True,
        )
    )
    new_invoice = _invoice(db_session, seller="完全新名称", amount="802.00", year=2026, month=7, day=20)
    new_invoice.seller_tax_id = tax_no
    new_txn = _txn(
        db_session,
        year=2026,
        month=7,
        day=19,
        amount="802.00",
        name="完全新银行名",
        counterparty_account=account,
    )
    db_session.commit()

    result = pm.auto_match(db_session, 2026, 7)

    assert result["matched"] == 0
    assert db_session.query(TaxInvoiceLink).filter_by(
        invoice_id=new_invoice.id,
        target_type="bank_transaction",
        target_id=new_txn.id,
    ).count() == 0
