from datetime import datetime
from decimal import Decimal

from app.models.business_partner import BusinessPartner
from app.models.purchase import ExternalPurchaseOrder, JackyunPurchaseOrder, JackyunPurchaseOrderLink
from app.models.tax import TaxInvoice, TaxInvoiceLink
from app.services import invoice_reconciliation
from app.services.invoice_reconciliation import reconcile


SUPPLIER = "日期匹配测试供应商"


def _po(db, no: str, ordered_at: datetime, amount: str):
    row = ExternalPurchaseOrder(
        external_order_id=no,
        platform="other",
        supplier_name=SUPPLIER,
        ordered_at=ordered_at,
        order_amount=Decimal(amount),
        paid_amount=Decimal(amount),
    )
    db.add(row)
    db.flush()
    return row


def _invoice(db, key: str, issue_date: datetime, amount: str):
    row = TaxInvoice(
        invoice_key=key,
        direction="input",
        invoice_number=key,
        status="issued",
        issue_date=issue_date,
        seller_name=SUPPLIER,
        total_amount=Decimal(amount),
    )
    db.add(row)
    db.flush()
    return row


def test_one_invoice_can_cover_multiple_prior_orders_in_order_date_sequence(db_session):
    _po(db_session, "DATE-FIFO-PO-1", datetime(2026, 1, 1, 10, 0), "40")
    _po(db_session, "DATE-FIFO-PO-2", datetime(2026, 1, 10, 10, 0), "60")
    _po(db_session, "DATE-FIFO-PO-3", datetime(2026, 2, 1, 10, 0), "50")
    invoice = _invoice(db_session, "DATE-FIFO-INV-1", datetime(2026, 1, 15, 12, 0), "100")

    result = reconcile(db_session, supplier=SUPPLIER)
    entry = result["suppliers"][0]
    invoices = [item for month in entry["months"] for item in month["invoices"]]
    matched = next(item for item in invoices if item["invoiceId"] == invoice.id)

    assert result["matchingRule"] == "invoice_issue_date_cutoff_then_order_date_fifo"
    assert matched["status"] == "matched"
    assert matched["coveredTotal"] == 100.0
    assert [item["orderNo"] for item in matched["covered"]] == [
        "DATE-FIFO-PO-1",
        "DATE-FIFO-PO-2",
    ]
    assert [item["consumed"] for item in matched["covered"]] == [40.0, 60.0]

    pending_by_no = {item["orderNo"]: item for item in entry["pendingOrders"]}
    assert pending_by_no["DATE-FIFO-PO-3"]["remaining"] == 50.0


def test_invoice_never_consumes_orders_created_after_issue_date(db_session):
    _po(db_session, "DATE-CUTOFF-PO-1", datetime(2026, 3, 1, 10, 0), "40")
    _po(db_session, "DATE-CUTOFF-PO-2", datetime(2026, 3, 10, 10, 0), "60")
    _po(db_session, "DATE-CUTOFF-PO-3", datetime(2026, 4, 1, 10, 0), "50")
    invoice = _invoice(db_session, "DATE-CUTOFF-INV-1", datetime(2026, 3, 5, 12, 0), "100")

    result = reconcile(db_session, supplier=SUPPLIER)
    entry = result["suppliers"][0]
    invoices = [item for month in entry["months"] for item in month["invoices"]]
    matched = next(item for item in invoices if item["invoiceId"] == invoice.id)

    assert matched["status"] == "short"
    assert matched["shortReason"] == "date_cutoff"
    assert matched["futureOrderCount"] == 2
    assert matched["coveredTotal"] == 40.0
    assert [item["orderNo"] for item in matched["covered"]] == ["DATE-CUTOFF-PO-1"]

    pending_by_no = {item["orderNo"]: item for item in entry["pendingOrders"]}
    assert pending_by_no["DATE-CUTOFF-PO-2"]["remaining"] == 60.0
    assert pending_by_no["DATE-CUTOFF-PO-3"]["remaining"] == 50.0



def test_manual_allocation_uses_explicit_amount_instead_of_expanding_to_invoice_total(db_session):
    order = _po(db_session, "MANUAL-ALLOC-PO", datetime(2026, 5, 1, 10, 0), "100")
    invoice = _invoice(db_session, "MANUAL-ALLOC-INV", datetime(2026, 5, 10, 12, 0), "100")
    db_session.add(TaxInvoiceLink(
        invoice_id=invoice.id,
        target_type="external_purchase_order",
        target_id=order.id,
        allocated_amount=Decimal("60"),
        match_method="manual",
        confirmed=True,
    ))
    db_session.flush()

    result = reconcile(db_session, supplier=SUPPLIER)
    entry = result["suppliers"][0]
    invoice_row = next(
        item for month in entry["months"] for item in month["invoices"]
        if item["invoiceId"] == invoice.id
    )

    assert invoice_row["manualLinked"] is True
    assert invoice_row["explicitLinked"] is True
    assert invoice_row["status"] == "short"
    assert invoice_row["coveredTotal"] == 60.0
    assert invoice_row["covered"][0]["allocatedAmount"] == 60.0
    assert invoice_row["covered"][0]["consumed"] == 60.0
    pending = {row["orderNo"]: row for row in entry["pendingOrders"]}
    assert pending["MANUAL-ALLOC-PO"]["remaining"] == 40.0


def test_source_ref_explicit_order_overrides_fifo_order_guess(db_session):
    _po(db_session, "SOURCE-REF-PO-1", datetime(2026, 6, 1, 10, 0), "100")
    second = _po(db_session, "SOURCE-REF-PO-2", datetime(2026, 6, 2, 10, 0), "100")
    invoice = _invoice(db_session, "SOURCE-REF-INV", datetime(2026, 6, 10, 12, 0), "100")
    db_session.add(TaxInvoiceLink(
        invoice_id=invoice.id,
        target_type="external_purchase_order",
        target_id=second.id,
        allocated_amount=Decimal("100"),
        match_method="source_ref",
        confirmed=True,
    ))
    db_session.flush()

    result = reconcile(db_session, supplier=SUPPLIER)
    entry = result["suppliers"][0]
    invoice_row = next(
        item for month in entry["months"] for item in month["invoices"]
        if item["invoiceId"] == invoice.id
    )

    assert invoice_row["status"] == "matched"
    assert invoice_row["manualLinked"] is False
    assert invoice_row["explicitLinked"] is True
    assert [row["orderNo"] for row in invoice_row["covered"]] == ["SOURCE-REF-PO-2"]
    pending = {row["orderNo"]: row for row in entry["pendingOrders"]}
    assert pending["SOURCE-REF-PO-1"]["remaining"] == 100.0
    assert "SOURCE-REF-PO-2" not in pending


def test_jackyun_manual_invoice_link_apportions_back_to_merged_workflow_orders(db_session):
    first = _po(db_session, "JY-INVOICE-PO-A", datetime(2026, 7, 1, 10, 0), "300")
    second = _po(db_session, "JY-INVOICE-PO-B", datetime(2026, 7, 2, 10, 0), "200")
    jpo = JackyunPurchaseOrder(
        jackyun_purch_id="JY-INVOICE-MERGED-ID",
        purch_no="JY-INVOICE-MERGED",
        supplier_name=SUPPLIER,
        amount=Decimal("500"),
    )
    db_session.add(jpo)
    db_session.flush()
    db_session.add_all([
        JackyunPurchaseOrderLink(
            po_id=first.id, jackyun_po_id=jpo.id,
            relation_kind="merged", alloc_amount=Decimal("300"),
        ),
        JackyunPurchaseOrderLink(
            po_id=second.id, jackyun_po_id=jpo.id,
            relation_kind="merged", alloc_amount=Decimal("200"),
        ),
    ])
    invoice = _invoice(db_session, "JY-INVOICE-INV", datetime(2026, 7, 10, 12, 0), "500")
    db_session.add(TaxInvoiceLink(
        invoice_id=invoice.id,
        target_type="jackyun_purchase_order",
        target_id=jpo.id,
        allocated_amount=Decimal("500"),
        match_method="manual",
        confirmed=True,
    ))
    db_session.flush()

    result = reconcile(db_session, supplier=SUPPLIER)
    entry = result["suppliers"][0]
    invoice_row = next(
        item for month in entry["months"] for item in month["invoices"]
        if item["invoiceId"] == invoice.id
    )

    assert invoice_row["status"] == "matched"
    assert invoice_row["coveredTotal"] == 500.0
    consumed = {row["orderNo"]: row["consumed"] for row in invoice_row["covered"]}
    assert consumed == {"JY-INVOICE-PO-A": 300.0, "JY-INVOICE-PO-B": 200.0}
    assert entry["pendingOrders"] == []


def test_jackyun_merged_group_without_allocation_is_explicit_review_not_guess(db_session):
    first = _po(db_session, "JY-REVIEW-PO-A", datetime(2026, 7, 1, 10, 0), "300")
    second = _po(db_session, "JY-REVIEW-PO-B", datetime(2026, 7, 2, 10, 0), "200")
    jpo = JackyunPurchaseOrder(
        jackyun_purch_id="JY-REVIEW-MERGED-ID",
        purch_no="JY-REVIEW-MERGED",
        supplier_name=SUPPLIER,
        amount=Decimal("500"),
    )
    db_session.add(jpo)
    db_session.flush()
    db_session.add_all([
        JackyunPurchaseOrderLink(po_id=first.id, jackyun_po_id=jpo.id, relation_kind="merged"),
        JackyunPurchaseOrderLink(po_id=second.id, jackyun_po_id=jpo.id, relation_kind="merged"),
    ])
    invoice = _invoice(db_session, "JY-REVIEW-INV", datetime(2026, 7, 10, 12, 0), "500")
    db_session.add(TaxInvoiceLink(
        invoice_id=invoice.id,
        target_type="jackyun_purchase_order",
        target_id=jpo.id,
        allocated_amount=Decimal("500"),
        match_method="manual",
        confirmed=True,
    ))
    db_session.flush()

    result = reconcile(db_session, supplier=SUPPLIER)
    entry = result["suppliers"][0]
    invoice_row = next(
        item for month in entry["months"] for item in month["invoices"]
        if item["invoiceId"] == invoice.id
    )

    assert invoice_row["status"] == "short"
    assert invoice_row["shortReason"] == "explicit_link_issue"
    assert "jackyun_group_allocation_missing" in invoice_row["explicitIssues"]
    assert invoice_row["coveredTotal"] == 0.0
    assert {row["orderNo"]: row["remaining"] for row in entry["pendingOrders"]} == {
        "JY-REVIEW-PO-A": 300.0,
        "JY-REVIEW-PO-B": 200.0,
    }



def test_bank_manual_link_does_not_override_purchase_fifo(db_session):
    """银行付款 manual 关联属于资金域，不能把采购对账误判成“人工采购关联”而跳过 FIFO。"""
    from app.models.bank import BankTransaction

    order = _po(db_session, "BANK-DOMAIN-PO", datetime(2026, 8, 1, 10, 0), "100")
    invoice = _invoice(db_session, "BANK-DOMAIN-INV", datetime(2026, 8, 10, 12, 0), "100")
    txn = BankTransaction(
        txn_date=datetime(2026, 8, 12).date(),
        direction="out",
        amount=Decimal("100"),
        counterparty_name=SUPPLIER,
        fingerprint="bank-domain-link-does-not-override-fifo",
    )
    db_session.add(txn)
    db_session.flush()
    db_session.add(TaxInvoiceLink(
        invoice_id=invoice.id,
        target_type="bank_transaction",
        target_id=txn.id,
        allocated_amount=Decimal("100"),
        match_method="manual",
        confirmed=True,
    ))
    db_session.flush()

    result = reconcile(db_session, supplier=SUPPLIER)
    entry = result["suppliers"][0]
    invoice_row = next(
        item for month in entry["months"] for item in month["invoices"]
        if item["invoiceId"] == invoice.id
    )

    assert invoice_row["explicitLinked"] is False
    assert invoice_row["manualLinked"] is False
    assert invoice_row["status"] == "matched"
    assert [row["orderNo"] for row in invoice_row["covered"]] == [order.external_order_id]
    assert invoice_row["covered"][0]["source"] == "auto"

def test_supplier_match_does_not_accept_arbitrary_prefix():
    assert (
        invoice_reconciliation.normalize_supplier("河北鸿鲲食品有限公司")
        == invoice_reconciliation.normalize_supplier("河北鸿鲲食品")
    )
    assert not invoice_reconciliation._matches(
        invoice_reconciliation.normalize_supplier("北京华"),
        invoice_reconciliation.normalize_supplier("北京华贸世纪"),
    )

def test_fifo_groups_different_raw_names_by_canonical_partner_id(db_session):
    """采购店铺名与开票法人名完全不同时，只要 partner_id 相同就必须进入同一 FIFO 池。"""
    partner = BusinessPartner(
        name="合锦（广州）供应链有限公司",
        normalized_name="合锦(广州)供应链有限公司",
        tax_no="91440101MA5AQUHB7W",
        roles=["supplier"],
        status="active",
    )
    db_session.add(partner)
    db_session.flush()

    order = ExternalPurchaseOrder(
        external_order_id="CANON-FIFO-PO-001",
        platform="1688",
        supplier_name="合锦1688店铺",
        supplier_partner_id=partner.id,
        ordered_at=datetime(2026, 7, 20, 10, 0),
        order_amount=Decimal("3080.00"),
        paid_amount=Decimal("3080.00"),
    )
    invoice = TaxInvoice(
        invoice_key="CANON-FIFO-INV-001",
        direction="input",
        invoice_number="CANON-FIFO-INV-001",
        status="issued",
        issue_date=datetime(2026, 7, 24, 12, 0),
        seller_name="合锦（广州）供应链有限公司",
        seller_tax_id="91440101MA5AQUHB7W",
        seller_partner_id=partner.id,
        total_amount=Decimal("3080.00"),
    )
    db_session.add_all([order, invoice])
    db_session.flush()

    result = reconcile(db_session, partner_id=partner.id)

    assert len(result["suppliers"]) == 1
    entry = result["suppliers"][0]
    assert entry["partnerId"] == partner.id
    assert entry["supplier"] == "合锦（广州）供应链有限公司"
    assert entry["orderCount"] == 1
    invoice_row = next(
        item for month in entry["months"] for item in month["invoices"]
        if item["invoiceId"] == invoice.id
    )
    assert invoice_row["status"] == "matched"
    assert invoice_row["coveredTotal"] == 3080.0
    assert [row["orderNo"] for row in invoice_row["covered"]] == [order.external_order_id]

