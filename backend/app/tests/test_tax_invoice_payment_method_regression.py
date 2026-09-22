from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.bank import BankTransaction
from app.models.tax import TaxInvoice, TaxInvoiceLink
from app.services import finance_corporate_payment_report_service as finance_payment_service
from app.services import payment_invoice_match_service as payment_service
from app.services import tax_invoice_service as service


def _invoice(db, direction: str, *, amount: str = "100") -> TaxInvoice:
    token = uuid4().hex[:10]
    row = TaxInvoice(
        invoice_key=f"payment-method-{direction}-{token}",
        invoice_number=f"PM-{direction}-{token}",
        direction=direction,
        status="issued",
        total_amount=Decimal(amount),
    )
    db.add(row)
    db.flush()
    return row


def _listed(db, invoice_id: int) -> dict:
    return next(
        item for item in service.list_invoices(db, limit=500)
        if item["id"] == invoice_id
    )


def test_output_invoice_payment_method_is_not_applicable_and_legacy_value_is_hidden(db_session):
    invoice = _invoice(db_session, "output")
    invoice.payment_method = "corporate"  # 历史错误数据
    db_session.commit()

    with pytest.raises(ValueError, match="不是进项发票"):
        service.set_invoice_payment_methods(
            db_session, [invoice.id], "personal", actor="pytest"
        )

    row = _listed(db_session, invoice.id)
    assert row["paymentMethod"] == ""
    assert row["manualPaymentMethod"] == ""
    assert row["bankPaymentStatus"] == "not_applicable"


def test_input_invoice_can_be_marked_personal_without_bank_evidence(db_session):
    invoice = _invoice(db_session, "input")

    service.set_invoice_payment_methods(
        db_session, [invoice.id], "personal", actor="pytest"
    )

    row = _listed(db_session, invoice.id)
    assert row["manualPaymentMethod"] == "personal"
    assert row["paymentMethod"] == "personal"
    assert row["bankPaymentStatus"] == "unmatched"
    assert row["bankPaidAmount"] == "0.00"
    assert row["bankRemainingAmount"] == "100.00"


def test_input_invoice_can_be_marked_platform_auto_debit_without_bank_evidence(db_session):
    invoice = _invoice(db_session, "input")

    service.set_invoice_payment_methods(
        db_session, [invoice.id], "platform_auto_debit", actor="pytest"
    )

    row = _listed(db_session, invoice.id)
    assert row["manualPaymentMethod"] == "platform_auto_debit"
    assert row["paymentMethod"] == "platform_auto_debit"
    assert row["bankPaymentStatus"] == "unmatched"


def test_input_invoice_cannot_fake_corporate_payment_manually(db_session):
    invoice = _invoice(db_session, "input")

    with pytest.raises(ValueError, match="对公付款必须由已确认银行付款生成"):
        service.set_invoice_payment_methods(
            db_session, [invoice.id], "corporate", actor="pytest"
        )

    db_session.refresh(invoice)
    assert invoice.payment_method == ""


def test_confirmed_bank_payment_overrides_display_but_preserves_manual_personal_fact(db_session):
    invoice = _invoice(db_session, "input")
    service.set_invoice_payment_methods(
        db_session, [invoice.id], "personal", actor="pytest"
    )
    txn = BankTransaction(
        txn_date=date(2026, 9, 1),
        direction="out",
        amount=Decimal("100"),
        counterparty_name="支付方式测试供应商",
        fingerprint=f"payment-method-{uuid4().hex}",
    )
    db_session.add(txn)
    db_session.commit()

    linked = payment_service.link(
        db_session,
        txn_id=txn.id,
        invoice_id=invoice.id,
        allocated_amount=Decimal("100"),
        actor="pytest",
    )

    db_session.refresh(invoice)
    assert invoice.payment_method == "personal"

    row = _listed(db_session, invoice.id)
    assert row["manualPaymentMethod"] == "personal"
    assert row["paymentMethod"] == "corporate"
    assert row["bankPaymentStatus"] == "matched"

    payment_service.unlink(db_session, linked["id"], actor="pytest")
    row = _listed(db_session, invoice.id)
    assert row["manualPaymentMethod"] == "personal"
    assert row["paymentMethod"] == "personal"
    assert row["bankPaymentStatus"] == "unmatched"


def test_partial_bank_payment_plus_explicit_personal_remainder_is_mixed(db_session):
    invoice = _invoice(db_session, "input")
    service.set_invoice_payment_methods(
        db_session, [invoice.id], "personal", actor="pytest"
    )
    txn = BankTransaction(
        txn_date=date(2026, 9, 2),
        direction="out",
        amount=Decimal("40"),
        counterparty_name="混合付款测试供应商",
        fingerprint=f"payment-method-partial-{uuid4().hex}",
    )
    db_session.add(txn)
    db_session.commit()

    payment_service.link(
        db_session,
        txn_id=txn.id,
        invoice_id=invoice.id,
        allocated_amount=Decimal("40"),
        actor="pytest",
    )
    db_session.refresh(invoice)
    assert invoice.payment_method == "personal"

    row = _listed(db_session, invoice.id)
    assert row["manualPaymentMethod"] == "personal"
    assert row["paymentMethod"] == "mixed"
    assert row["bankPaymentStatus"] == "partial"
    assert row["bankPaidAmount"] == "40.00"
    assert row["bankRemainingAmount"] == "60.00"


def test_unconfirmed_bank_link_does_not_override_manual_personal(db_session):
    invoice = _invoice(db_session, "input")
    service.set_invoice_payment_methods(
        db_session, [invoice.id], "personal", actor="pytest"
    )
    txn = BankTransaction(
        txn_date=date(2026, 9, 3),
        direction="out",
        amount=Decimal("100"),
        counterparty_name="未确认支付方式测试供应商",
        fingerprint=f"payment-method-unconfirmed-{uuid4().hex}",
    )
    db_session.add(txn)
    db_session.flush()
    db_session.add(
        TaxInvoiceLink(
            invoice_id=invoice.id,
            target_type="bank_transaction",
            target_id=txn.id,
            allocated_amount=Decimal("100"),
            match_method="manual",
            confirmed=False,
        )
    )
    db_session.commit()

    row = _listed(db_session, invoice.id)
    assert row["manualPaymentMethod"] == "personal"
    assert row["paymentMethod"] == "personal"
    assert row["bankPaymentStatus"] == "unmatched"


def test_invalid_or_red_input_invoice_cannot_be_marked_personal(db_session):
    invoice = _invoice(db_session, "input", amount="-100")
    invoice.status = "red"
    db_session.commit()

    with pytest.raises(ValueError, match="红冲"):
        service.set_invoice_payment_methods(
            db_session, [invoice.id], "personal", actor="pytest"
        )


def test_auto_bank_match_preserves_manual_personal_fact(db_session):
    invoice = _invoice(db_session, "input")
    invoice.seller_name = "自动付款匹配供应商"
    invoice.issue_date = datetime(2026, 9, 10)
    service.set_invoice_payment_methods(
        db_session, [invoice.id], "personal", actor="pytest"
    )

    txn = BankTransaction(
        txn_date=date(2026, 9, 12),
        direction="out",
        amount=Decimal("100"),
        counterparty_name="自动付款匹配供应商",
        fingerprint=f"payment-method-auto-{uuid4().hex}",
    )
    db_session.add(txn)
    db_session.commit()

    result = payment_service.auto_match(db_session, 2026, 9, actor="pytest")
    assert result["matched"] >= 1

    db_session.refresh(invoice)
    assert invoice.payment_method == "personal"
    row = _listed(db_session, invoice.id)
    assert row["manualPaymentMethod"] == "personal"
    assert row["paymentMethod"] == "corporate"
    assert row["bankPaymentStatus"] == "matched"


def test_unmatched_input_invoice_defaults_to_personal_from_bank_evidence_rule(db_session):
    invoice = _invoice(db_session, "input")

    row = _listed(db_session, invoice.id)

    assert row["manualPaymentMethod"] == ""
    assert row["bankPaymentStatus"] == "unmatched"
    assert row["paymentMethod"] == "personal"


def test_partial_bank_payment_defaults_to_mixed_without_manual_flag(db_session):
    invoice = _invoice(db_session, "input")
    txn = BankTransaction(
        txn_date=date(2026, 9, 21),
        direction="out",
        amount=Decimal("40"),
        counterparty_name="部分银行付款默认混合供应商",
        fingerprint=f"payment-method-partial-default-{uuid4().hex}",
    )
    db_session.add(txn)
    db_session.commit()

    payment_service.link(
        db_session,
        txn_id=txn.id,
        invoice_id=invoice.id,
        allocated_amount=Decimal("40"),
        actor="pytest",
    )

    row = _listed(db_session, invoice.id)
    assert row["manualPaymentMethod"] == ""
    assert row["bankPaymentStatus"] == "partial"
    assert row["bankPaidAmount"] == "40.00"
    assert row["bankRemainingAmount"] == "60.00"
    assert row["paymentMethod"] == "mixed"


@pytest.mark.parametrize(
    ("bank_status", "finance_status", "expected"),
    [
        ("unmatched", "unpaid", "personal"),
        ("partial", "partial", "mixed"),
        ("matched", "paid", "corporate"),
    ],
)
def test_finance_export_and_invoice_ledger_share_payment_classification(
    db_session, bank_status, finance_status, expected
):
    invoice = _invoice(db_session, "input")

    ledger_method = service.payment_method_context(invoice, bank_status)["paymentMethod"]
    finance_method, _, _ = finance_payment_service._payment_source(
        db_session, invoice, finance_status
    )

    assert ledger_method == expected
    assert finance_method == expected
