from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.models.sales import SalesOrder, SalesOrderItem
from app.models.tax import TaxInvoice, TaxInvoiceImport, TaxInvoiceLink
from app.services.tax_accounting_service import monthly_ledger


def _batch(db_session) -> TaxInvoiceImport:
    row = TaxInvoiceImport(
        original_name=f"pytest-tax-{uuid4().hex}.xlsx",
        stored_path=f"/tmp/pytest-{uuid4().hex}.xlsx",
        sha256=uuid4().hex + uuid4().hex,
        size=100,
        source_system="tax_export",
        period_year=2026,
        period_month=8,
        status="parsed",
        lifecycle="active",
        row_count=1,
        recognized_row_count=1,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_invoice_values_win_over_business_data(db_session):
    batch = _batch(db_session)
    sales = SalesOrder(
        order_no=f"PYTEST-SALES-{uuid4().hex}",
        order_amount=Decimal("999.00"),
        paid_amount=Decimal("999.00"),
        currency="CNY",
        ordered_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    db_session.add(sales)
    db_session.flush()
    db_session.add(SalesOrderItem(
        order_id=sales.id,
        sku_code="PYTEST-COFFEE",
        goods_name="测试咖啡",
        quantity=Decimal("9"),
        unit_price=Decimal("111.00"),
        amount=Decimal("999.00"),
    ))

    invoice = TaxInvoice(
        invoice_key=f"pytest|{uuid4().hex}",
        invoice_number=f"INV-{uuid4().hex[:12]}",
        direction="output",
        status="issued",
        issue_date=datetime(2026, 8, 15, tzinfo=timezone.utc),
        seller_name="测试销售方",
        buyer_name="测试购买方",
        amount_excl_tax=Decimal("1000.00"),
        tax_amount=Decimal("130.00"),
        total_amount=Decimal("1130.00"),
        source_system="tax_export",
        source_import_id=batch.id,
        source_row_index=1,
        raw={
            "货物或应税劳务、服务名称": "测试咖啡",
            "数量": "10",
            "不含税单价": "100",
            "金额": "1000",
            "税率": "13%",
            "税额": "130",
        },
    )
    db_session.add(invoice)
    db_session.flush()
    db_session.add(TaxInvoiceLink(
        invoice_id=invoice.id,
        target_type="sales_order",
        target_id=sales.id,
        allocated_amount=Decimal("1130.00"),
        match_method="pytest",
        confidence=Decimal("1.0000"),
        confirmed=True,
    ))
    db_session.flush()

    ledger = monthly_ledger(db_session, 2026, 8)
    item = ledger["items"][0]
    assert item["totalAmount"] == "1130.00"
    assert item["businessAmount"] == "999.00"
    assert item["amountDifference"] == "131.00"
    assert item["invoiceLine"]["quantity"] == "10"
    assert item["businessQuantity"] == "9"
    assert item["quantityDifference"] == "1"
    assert item["accountingSource"] == "tax_invoice"
    assert ledger["summary"]["readyForAccountingDraft"] is False


def test_missing_invoice_detail_never_falls_back_to_business_quantity_or_price(db_session):
    batch = _batch(db_session)
    sales = SalesOrder(
        order_no=f"PYTEST-SALES-{uuid4().hex}",
        order_amount=Decimal("500.00"),
        paid_amount=Decimal("500.00"),
        currency="CNY",
        ordered_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    db_session.add(sales)
    db_session.flush()
    db_session.add(SalesOrderItem(
        order_id=sales.id,
        sku_code="PYTEST-COFFEE-2",
        goods_name="测试咖啡2",
        quantity=Decimal("5"),
        unit_price=Decimal("100.00"),
        amount=Decimal("500.00"),
    ))

    invoice = TaxInvoice(
        invoice_key=f"pytest|{uuid4().hex}",
        invoice_number=f"INV-{uuid4().hex[:12]}",
        direction="output",
        status="issued",
        issue_date=datetime(2026, 8, 21, tzinfo=timezone.utc),
        amount_excl_tax=Decimal("442.48"),
        tax_amount=Decimal("57.52"),
        total_amount=Decimal("500.00"),
        source_system="tax_export",
        source_import_id=batch.id,
        source_row_index=1,
        raw={"价税合计": "500.00", "税额": "57.52"},
    )
    db_session.add(invoice)
    db_session.flush()
    db_session.add(TaxInvoiceLink(
        invoice_id=invoice.id,
        target_type="sales_order",
        target_id=sales.id,
        allocated_amount=Decimal("500.00"),
        match_method="pytest",
        confidence=Decimal("1.0000"),
        confirmed=True,
    ))
    db_session.flush()

    ledger = monthly_ledger(db_session, 2026, 8)
    item = ledger["items"][0]
    assert item["invoiceLine"]["quantity"] is None
    assert item["invoiceLine"]["unitPriceExclTax"] is None
    assert item["businessQuantity"] == "5"
    assert item["quantityDifference"] is None
    assert item["businessDataRole"] == "reconciliation_only"
    assert any("禁止用吉客云/手工数据替代" in reason for reason in ledger["blockers"][0]["reasons"])
