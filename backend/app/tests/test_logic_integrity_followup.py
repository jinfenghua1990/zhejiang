from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.config import settings
from app.models.purchase import JackyunPurchaseOrder
from app.services import procurement_board_service as board
from app.services import procurement_chain_service as chain
from app.services import procurement_workbench_service as workbench
from app.services.inbound_document_view import _day_start
from app.services.purchase_service import _jackyun_po_date


def test_procurement_money_helpers_keep_decimal_until_serialization():
    row = {
        "amount": "0.30",
        "paidAmount": "0.30",
        "paidOn1688": True,
        "settlement": [],
    }
    assert board._paid_amount(row, cap=Decimal("0.30")) == Decimal("0.30")
    assert workbench._paid_amount(row, cap=Decimal("0.30")) == Decimal("0.30")

    breakdown = board._payment_breakdown({
        "goodsTotal": "0.10",
        "freight": "0.20",
        "expenses": [],
        "discount": "0",
        "paidOn1688": True,
        "paidAmount": "0.30",
    })
    assert breakdown["totalDue"] == 0.3
    assert breakdown["unpaidAmount"] == 0.0

    closure = chain._po_amount_closure(
        [
            {"allocAmount": "0.10", "amount": "99"},
            {"allocAmount": "0.20", "amount": "99"},
        ],
        Decimal("0.30"),
    )
    assert closure["allocTotal"] == 0.3
    assert closure["gap"] == 0.0
    assert closure["closed"] is True


def test_single_legacy_po_without_explicit_allocation_is_not_false_positive():
    closure = chain._po_amount_closure(
        [{"allocAmount": None, "amount": "80.00"}],
        Decimal("100.00"),
    )
    assert closure["gap"] == 20.0
    assert closure["closed"] is True


def test_business_date_helpers_are_shanghai_aware():
    tz = ZoneInfo(settings.TZ)
    start = _day_start(date(2026, 9, 1))
    assert start.tzinfo is not None
    assert start.utcoffset() == tz.utcoffset(start)

    jpo = JackyunPurchaseOrder(
        jackyun_purch_id="TZ-PO-001",
        raw={"date": "2026-09-01 00:30:00"},
    )
    parsed = _jackyun_po_date(jpo)
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == tz.utcoffset(parsed)

def test_month_services_use_same_shanghai_boundary(db_session):
    from datetime import datetime, timezone

    from app.models.jackyun import JackyunGoodsDocument
    from app.models.tax import TaxInvoice, TaxInvoiceImport, TaxInvoiceImportRecord
    from app.services import sales_outbound_report_service
    from app.services import tax_accounting_service
    from app.services import tax_finance_summary_service

    # 2096-12-31 16:30 UTC = 2097-01-01 00:30 Asia/Shanghai。
    boundary = datetime(2096, 12, 31, 16, 30, tzinfo=timezone.utc)

    batch = TaxInvoiceImport(
        original_name="boundary-tax.csv",
        stored_path="/tmp/boundary-tax.csv",
        sha256="boundary-tax-2097",
        lifecycle="active",
        source_system="tax_export",
        period_year=2097,
        period_month=1,
    )
    db_session.add(batch)
    db_session.flush()
    invoice = TaxInvoice(
        invoice_key="BOUNDARY-TAX-2097",
        invoice_number="BOUNDARY-TAX-2097",
        direction="output",
        status="issued",
        issue_date=boundary,
        amount_excl_tax=Decimal("90"),
        tax_amount=Decimal("10"),
        total_amount=Decimal("100"),
        source_system="tax_export",
        source_import_id=batch.id,
        source_row_index=1,
        raw={"财务大类": "商品", "价税合计": "100", "金额": "90", "税额": "10"},
    )
    db_session.add(invoice)
    db_session.flush()
    db_session.add(TaxInvoiceImportRecord(
        import_id=batch.id,
        row_index=1,
        invoice_id=invoice.id,
        recognition_status="recognized",
        payload={"财务大类": "商品", "价税合计": "100", "金额": "90", "税额": "10"},
    ))
    outbound = JackyunGoodsDocument(
        document_type="outbound",
        goodsdoc_no="BOUNDARY-OUTBOUND-2097",
        document_at=boundary,
    )
    db_session.add(outbound)
    db_session.flush()

    jan_ledger = tax_accounting_service.monthly_ledger(db_session, 2097, 1)
    dec_ledger = tax_accounting_service.monthly_ledger(db_session, 2096, 12)
    assert jan_ledger["summary"]["invoiceCount"] == 1
    assert dec_ledger["summary"]["invoiceCount"] == 0

    jan_summary = tax_finance_summary_service.build_finance_summary(db_session, 2097, 1)
    dec_summary = tax_finance_summary_service.build_finance_summary(db_session, 2096, 12)
    assert jan_summary["summary"]["detailCount"] == 1
    assert dec_summary["summary"]["detailCount"] == 0

    periods = {
        (row["year"], row["month"]): row["docCount"]
        for row in sales_outbound_report_service.months_with_data(db_session)
    }
    assert periods[(2097, 1)] == 1
    assert (2096, 12) not in periods

    jan_outbound = sales_outbound_report_service.build_report(db_session, 2097, 1)
    dec_outbound = sales_outbound_report_service.build_report(db_session, 2096, 12)
    assert jan_outbound["summary"]["docCount"] == 1
    assert dec_outbound["summary"]["docCount"] == 0
