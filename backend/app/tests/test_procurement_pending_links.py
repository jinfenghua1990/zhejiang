from decimal import Decimal

from app.models.purchase import ExternalPurchaseOrder
from app.models.tax import TaxInvoice, TaxInvoiceLink
from app.services.procurement_chain_service import auto_confirm_pending_links, list_pending


def test_pending_invoice_for_workflow_only_order_uses_negative_workbench_id(db_session):
    po = ExternalPurchaseOrder(
        external_order_id="PDD-PENDING-001",
        platform="pdd",
        supplier_name="待确认供应商",
        paid_amount=Decimal("100"),
    )
    invoice = TaxInvoice(
        invoice_key="PENDING-INV-001",
        direction="input",
        invoice_number="PENDING-INV-001",
        status="issued",
        seller_name="待确认供应商",
        total_amount=Decimal("100"),
    )
    db_session.add_all([po, invoice])
    db_session.flush()
    db_session.add(
        TaxInvoiceLink(
            invoice_id=invoice.id,
            target_type="external_purchase_order",
            target_id=po.id,
            allocated_amount=Decimal("100"),
            match_method="auto",
            confirmed=False,
        )
    )
    db_session.flush()

    result = list_pending(db_session)

    item = next(row for row in result["items"] if row["orderNo"] == "PDD-PENDING-001")
    assert item["kind"] == "invoice"
    assert item["orderId"] == -po.id
    assert item["orderNo"] == "PDD-PENDING-001"


def test_auto_confirm_keeps_invoice_with_unknown_allocation_pending(db_session):
    po = ExternalPurchaseOrder(
        external_order_id="PDD-UNKNOWN-AMOUNT-001",
        platform="pdd",
        supplier_name="金额未知供应商",
        paid_amount=Decimal("100"),
    )
    invoice = TaxInvoice(
        invoice_key="PENDING-UNKNOWN-AMOUNT-001",
        direction="input",
        invoice_number="PENDING-UNKNOWN-AMOUNT-001",
        status="issued",
        seller_name="金额未知供应商",
        total_amount=Decimal("100"),
        match_status="unmatched",
    )
    db_session.add_all([po, invoice])
    db_session.flush()
    link = TaxInvoiceLink(
        invoice_id=invoice.id,
        target_type="external_purchase_order",
        target_id=po.id,
        allocated_amount=None,
        match_method="auto",
        confirmed=False,
    )
    db_session.add(link)
    db_session.flush()

    result = auto_confirm_pending_links(db_session)

    db_session.refresh(link)
    db_session.refresh(invoice)
    assert link.confirmed is False
    assert invoice.match_status == "unmatched"
    assert result["pendingInvoiceAmount"] == 1
