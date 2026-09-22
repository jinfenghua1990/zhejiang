from datetime import date, datetime
from decimal import Decimal

import pytest

from app.models.bank import BankTransaction
from app.models.finance import ArchiveFile, FinanceDeliveryFile, FinanceDeliveryPackage, MonthlyFinancePeriod
from app.models.purchase import ExternalPurchaseOrder, PurchaseInvoice, PurchaseInvoiceLink
from app.models.tax import TaxInvoice, TaxInvoiceLink
from app.services import finance_service, purchase_service, tax_invoice_service
from app.services.consumable_service import _coverage_metrics
from app.services.payment_invoice_match_service import (
    _split_match_invoice_to_txns,
    _split_match_txn_to_invoices,
)


def test_consumable_coverage_uses_consumable_units():
    support, coverage, gap = _coverage_metrics(
        Decimal("1000"),
        Decimal("1000"),
        Decimal("2"),
    )
    assert support == Decimal("500")
    assert coverage == Decimal("100")
    assert gap == Decimal("0")


def test_sent_finance_package_cannot_be_deleted(db_session):
    period = MonthlyFinancePeriod(
        company="pytest-company",
        period_year=2026,
        period_month=8,
        status="SENT",
    )
    db_session.add(period)
    db_session.flush()
    package = FinanceDeliveryPackage(period_id=period.id, version=1, status="SENT")
    db_session.add(package)
    db_session.flush()

    with pytest.raises(ValueError, match="禁止删除"):
        finance_service.delete_delivery_package(db_session, package.id)


def test_archive_used_by_delivery_package_cannot_be_deleted(db_session):
    period = MonthlyFinancePeriod(
        company="pytest-company",
        period_year=2026,
        period_month=8,
        status="PACKAGED",
    )
    db_session.add(period)
    db_session.flush()
    archive = ArchiveFile(
        company="pytest-company",
        category="bank",
        original_name="test.xlsx",
        stored_path="/tmp/not-used-because-delete-is-blocked.xlsx",
        sha256="a" * 64,
        period_year=2026,
        period_month=8,
        version=1,
    )
    db_session.add(archive)
    db_session.flush()
    package = FinanceDeliveryPackage(period_id=period.id, version=1, status="PACKAGED")
    db_session.add(package)
    db_session.flush()
    db_session.add(FinanceDeliveryFile(package_id=package.id, archive_file_id=archive.id))
    db_session.flush()

    with pytest.raises(ValueError, match="已被财务交付包引用"):
        finance_service.delete_archive_file(db_session, archive.id)


def test_purchase_invoice_cumulative_order_limit(db_session):
    po = ExternalPurchaseOrder(
        external_order_id="AUDIT-PO-100",
        platform="other",
        paid_amount=Decimal("100"),
        order_amount=Decimal("100"),
    )
    invoice_a = PurchaseInvoice(invoice_no="AUDIT-A", invoice_amount=Decimal("100"))
    invoice_b = PurchaseInvoice(invoice_no="AUDIT-B", invoice_amount=Decimal("100"))
    db_session.add_all([po, invoice_a, invoice_b])
    db_session.flush()
    db_session.add(PurchaseInvoiceLink(invoice_id=invoice_a.id, po_id=po.id, allocated_amount=Decimal("80")))
    db_session.flush()

    with pytest.raises(ValueError, match="累计发票分摊"):
        purchase_service.link_invoice(db_session, invoice_b, po, Decimal("30"))


def test_tax_invoice_cumulative_invoice_limit(db_session):
    po1 = ExternalPurchaseOrder(
        external_order_id="AUDIT-TAX-PO-1",
        platform="other",
        paid_amount=Decimal("100"),
        order_amount=Decimal("100"),
    )
    po2 = ExternalPurchaseOrder(
        external_order_id="AUDIT-TAX-PO-2",
        platform="other",
        paid_amount=Decimal("100"),
        order_amount=Decimal("100"),
    )
    invoice = TaxInvoice(
        invoice_key="AUDIT-TAX-INV-1",
        direction="input",
        invoice_number="AUDIT-TAX-INV-1",
        status="issued",
        total_amount=Decimal("100"),
    )
    db_session.add_all([po1, po2, invoice])
    db_session.flush()
    db_session.add(
        TaxInvoiceLink(
            invoice_id=invoice.id,
            target_type="external_purchase_order",
            target_id=po1.id,
            allocated_amount=Decimal("80"),
            match_method="manual",
            confirmed=True,
        )
    )
    db_session.flush()

    with pytest.raises(ValueError, match="超过票面金额"):
        tax_invoice_service.link_purchase_order(
            db_session,
            invoice_id=invoice.id,
            target_type="external_purchase_order",
            target_id=po2.id,
            allocated_amount=Decimal("30"),
        )


def test_bank_split_allocation_is_independent_of_business_match_status(db_session):
    txn1 = BankTransaction(
        txn_date=date(2026, 8, 1),
        direction="out",
        amount=Decimal("5000"),
        counterparty_name="测试供应商",
        fingerprint="audit-payment-1",
    )
    txn2 = BankTransaction(
        txn_date=date(2026, 8, 2),
        direction="out",
        amount=Decimal("1080"),
        counterparty_name="测试供应商",
        fingerprint="audit-payment-2",
    )
    inv1 = TaxInvoice(
        invoice_key="AUDIT-PAY-INV-1",
        direction="input",
        invoice_number="AUDIT-PAY-INV-1",
        issue_date=datetime(2026, 8, 1),
        seller_name="测试供应商",
        total_amount=Decimal("3080"),
        status="issued",
        match_status="unmatched",
    )
    inv2 = TaxInvoice(
        invoice_key="AUDIT-PAY-INV-2",
        direction="input",
        invoice_number="AUDIT-PAY-INV-2",
        issue_date=datetime(2026, 8, 1),
        seller_name="测试供应商",
        total_amount=Decimal("3000"),
        status="issued",
        match_status="unmatched",
    )
    db_session.add_all([txn1, txn2, inv1, inv2])
    db_session.flush()

    count, allocated = _split_match_txn_to_invoices(db_session, txn1, [inv1, inv2], "pytest")
    assert count == 2
    assert allocated == Decimal("5000.0000")
    assert inv1.match_status == "unmatched"
    assert inv2.match_status == "unmatched"

    count2, allocated2 = _split_match_invoice_to_txns(db_session, inv2, [txn2], "pytest")
    assert count2 == 1
    assert allocated2 == Decimal("3000.0000")
    assert inv2.match_status == "unmatched"


def test_unlink_purchase_recalculates_only_business_domain(db_session):
    po1 = ExternalPurchaseOrder(
        external_order_id="AUDIT-UNLINK-PO-1",
        platform="other",
        paid_amount=Decimal("60"),
        order_amount=Decimal("60"),
    )
    po2 = ExternalPurchaseOrder(
        external_order_id="AUDIT-UNLINK-PO-2",
        platform="other",
        paid_amount=Decimal("40"),
        order_amount=Decimal("40"),
    )
    invoice = TaxInvoice(
        invoice_key="AUDIT-UNLINK-INV",
        direction="input",
        invoice_number="AUDIT-UNLINK-INV",
        status="issued",
        total_amount=Decimal("100"),
        match_status="unmatched",
    )
    txn = BankTransaction(
        txn_date=date(2026, 8, 3),
        direction="out",
        amount=Decimal("100"),
        counterparty_name="测试供应商",
        fingerprint="audit-unlink-bank",
    )
    db_session.add_all([po1, po2, invoice, txn])
    db_session.flush()

    first = tax_invoice_service.link_purchase_order(
        db_session,
        invoice_id=invoice.id,
        target_type="external_purchase_order",
        target_id=po1.id,
        allocated_amount=Decimal("60"),
    )
    second = tax_invoice_service.link_purchase_order(
        db_session,
        invoice_id=invoice.id,
        target_type="external_purchase_order",
        target_id=po2.id,
        allocated_amount=Decimal("40"),
    )
    db_session.refresh(invoice)
    assert invoice.match_status == "matched"

    # 银行链接存在也不能让采购匹配状态继续保持 matched。
    db_session.add(TaxInvoiceLink(
        invoice_id=invoice.id,
        target_type="bank_transaction",
        target_id=txn.id,
        allocated_amount=Decimal("100"),
        match_method="manual",
        confirmed=True,
    ))
    db_session.commit()

    tax_invoice_service.unlink_purchase(db_session, second["id"], actor="pytest")
    db_session.refresh(invoice)
    assert invoice.match_status == "partial"

    active_purchase = db_session.query(TaxInvoiceLink).filter(
        TaxInvoiceLink.invoice_id == invoice.id,
        TaxInvoiceLink.target_type == "external_purchase_order",
        TaxInvoiceLink.match_method != "rejected",
    ).all()
    assert sum((row.allocated_amount for row in active_purchase), Decimal("0")) == Decimal("60")
    assert first["id"] == active_purchase[0].id



def test_invoice_business_match_and_bank_payment_status_are_independent(db_session):
    """发票↔业务单据与发票↔银行付款必须是两个独立状态域。"""
    business_po = ExternalPurchaseOrder(
        external_order_id="DOMAIN-SPLIT-PO-1",
        platform="other",
        paid_amount=Decimal("100"),
        order_amount=Decimal("100"),
    )
    business_invoice = TaxInvoice(
        invoice_key="DOMAIN-SPLIT-INV-BUSINESS",
        direction="input",
        invoice_number="DOMAIN-SPLIT-INV-BUSINESS",
        status="issued",
        total_amount=Decimal("100"),
        match_status="matched",
    )
    paid_invoice = TaxInvoice(
        invoice_key="DOMAIN-SPLIT-INV-PAID",
        direction="input",
        invoice_number="DOMAIN-SPLIT-INV-PAID",
        status="issued",
        total_amount=Decimal("100"),
        match_status="unmatched",
    )
    payment = BankTransaction(
        txn_date=date(2026, 9, 20),
        direction="out",
        amount=Decimal("100"),
        counterparty_name="状态隔离供应商",
        fingerprint="domain-split-payment",
    )
    db_session.add_all([business_po, business_invoice, paid_invoice, payment])
    db_session.flush()
    db_session.add_all([
        TaxInvoiceLink(
            invoice_id=business_invoice.id,
            target_type="external_purchase_order",
            target_id=business_po.id,
            allocated_amount=Decimal("100"),
            match_method="manual",
            confirmed=True,
        ),
        TaxInvoiceLink(
            invoice_id=paid_invoice.id,
            target_type="bank_transaction",
            target_id=payment.id,
            allocated_amount=Decimal("100"),
            match_method="manual",
            confirmed=True,
        ),
    ])
    db_session.commit()

    rows = {
        row["id"]: row
        for row in tax_invoice_service.list_invoices(db_session, direction="input", limit=500)
    }

    business = rows[business_invoice.id]
    assert business["businessMatchStatus"] == "matched"
    assert business["bankPaymentStatus"] == "unmatched"
    assert business["paymentMethod"] == "personal"
    assert [link["targetType"] for link in business["links"]] == ["external_purchase_order"]

    paid = rows[paid_invoice.id]
    assert paid["businessMatchStatus"] == "unmatched"
    assert paid["bankPaymentStatus"] == "matched"
    assert Decimal(paid["bankPaidAmount"]) == Decimal("100.00")
    assert Decimal(paid["bankRemainingAmount"]) == Decimal("0.00")
    assert paid["paymentMethod"] == "corporate"
    # 银行付款链接绝不能混进发票详情的采购/销售业务 links。
    assert paid["links"] == []

    db_session.refresh(paid_invoice)
    assert paid_invoice.match_status == "unmatched"


def test_invoice_partial_bank_payment_does_not_change_business_match(db_session):
    invoice = TaxInvoice(
        invoice_key="DOMAIN-SPLIT-INV-PARTIAL",
        direction="input",
        invoice_number="DOMAIN-SPLIT-INV-PARTIAL",
        status="issued",
        total_amount=Decimal("100"),
        match_status="unmatched",
    )
    payment = BankTransaction(
        txn_date=date(2026, 9, 20),
        direction="out",
        amount=Decimal("40"),
        counterparty_name="部分付款供应商",
        fingerprint="domain-split-payment-partial",
    )
    db_session.add_all([invoice, payment])
    db_session.flush()
    db_session.add(TaxInvoiceLink(
        invoice_id=invoice.id,
        target_type="bank_transaction",
        target_id=payment.id,
        allocated_amount=Decimal("40"),
        match_method="manual",
        confirmed=True,
    ))
    db_session.commit()

    row = next(
        row for row in tax_invoice_service.list_invoices(db_session, direction="input", limit=500)
        if row["id"] == invoice.id
    )
    assert row["businessMatchStatus"] == "unmatched"
    assert row["bankPaymentStatus"] == "partial"
    assert Decimal(row["bankPaidAmount"]) == Decimal("40.00")
    assert Decimal(row["bankRemainingAmount"]) == Decimal("60.00")
    assert row["paymentMethod"] == "mixed"



def test_invoice_business_partial_status_has_own_amounts_and_ignores_bank_links(db_session):
    po = ExternalPurchaseOrder(
        external_order_id="DOMAIN-BUSINESS-PARTIAL",
        platform="other",
        paid_amount=Decimal("60"),
        order_amount=Decimal("60"),
    )
    invoice = TaxInvoice(
        invoice_key="DOMAIN-BUSINESS-PARTIAL-INV",
        direction="input",
        invoice_number="DOMAIN-BUSINESS-PARTIAL-INV",
        status="issued",
        total_amount=Decimal("100"),
        match_status="unmatched",
    )
    txn = BankTransaction(
        txn_date=date(2026, 9, 20),
        direction="out",
        amount=Decimal("100"),
        counterparty_name="业务银行隔离供应商",
        fingerprint="domain-business-partial-bank",
    )
    db_session.add_all([po, invoice, txn])
    db_session.flush()
    db_session.add_all([
        TaxInvoiceLink(
            invoice_id=invoice.id,
            target_type="external_purchase_order",
            target_id=po.id,
            allocated_amount=Decimal("60"),
            match_method="manual",
            confirmed=True,
        ),
        TaxInvoiceLink(
            invoice_id=invoice.id,
            target_type="bank_transaction",
            target_id=txn.id,
            allocated_amount=Decimal("100"),
            match_method="manual",
            confirmed=True,
        ),
    ])
    db_session.commit()

    row = next(
        item for item in tax_invoice_service.list_invoices(db_session, direction="input", limit=500)
        if item["id"] == invoice.id
    )
    assert row["businessMatchStatus"] == "partial"
    assert Decimal(row["businessMatchedAmount"]) == Decimal("60.00")
    assert Decimal(row["businessRemainingAmount"]) == Decimal("40.00")
    assert row["bankPaymentStatus"] == "matched"
    assert Decimal(row["bankPaidAmount"]) == Decimal("100.00")
    assert Decimal(row["bankRemainingAmount"]) == Decimal("0.00")


def test_unconfirmed_business_link_does_not_count_as_business_match(db_session):
    po = ExternalPurchaseOrder(
        external_order_id="DOMAIN-BUSINESS-UNCONFIRMED",
        platform="other",
        paid_amount=Decimal("100"),
        order_amount=Decimal("100"),
    )
    invoice = TaxInvoice(
        invoice_key="DOMAIN-BUSINESS-UNCONFIRMED-INV",
        direction="input",
        invoice_number="DOMAIN-BUSINESS-UNCONFIRMED-INV",
        status="issued",
        total_amount=Decimal("100"),
        match_status="unmatched",
    )
    db_session.add_all([po, invoice])
    db_session.flush()
    db_session.add(TaxInvoiceLink(
        invoice_id=invoice.id,
        target_type="external_purchase_order",
        target_id=po.id,
        allocated_amount=Decimal("100"),
        match_method="manual",
        confirmed=False,
    ))
    db_session.commit()

    row = next(
        item for item in tax_invoice_service.list_invoices(db_session, direction="input", limit=500)
        if item["id"] == invoice.id
    )
    assert row["businessMatchStatus"] == "unmatched"
    assert Decimal(row["businessMatchedAmount"]) == Decimal("0.00")
    assert row["links"] == []
