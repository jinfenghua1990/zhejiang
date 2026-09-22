from decimal import Decimal

from app.models.jackyun import JackyunGoodsDocument
from app.models.procurement_chain import ProcurementChainLink
from app.models.purchase import ExternalPurchaseOrder
from app.models.tax import TaxInvoice, TaxInvoiceLink
from app.services.inbound_document_view import list_inbound_documents


def test_inbound_detail_traces_allocated_input_invoice(db_session):
    order = ExternalPurchaseOrder(
        external_order_id="INBOUND-INVOICE-ORDER-001",
        platform="1688",
        supplier_name="入库发票供应商",
    )
    document = JackyunGoodsDocument(
        document_type="inbound",
        goodsdoc_no="RK-INVOICE-001",
        supplier_name="入库发票供应商",
        total_amount=Decimal("100"),
    )
    db_session.add_all([order, document])
    db_session.flush()
    db_session.add(ProcurementChainLink(
        external_po_id=order.id,
        target_type="inbound",
        target_id=document.id,
        match_method="file_import",
        confirmed=True,
    ))
    invoice = TaxInvoice(
        invoice_key="pytest-inbound-invoice|001",
        direction="input",
        invoice_number="INV-INBOUND-001",
        seller_name="入库发票供应商",
        status="issued",
        total_amount=Decimal("80"),
    )
    db_session.add(invoice)
    db_session.flush()
    db_session.add(TaxInvoiceLink(
        invoice_id=invoice.id,
        target_type="external_purchase_order",
        target_id=order.id,
        allocated_amount=Decimal("80"),
        match_method="manual",
        confirmed=True,
    ))
    db_session.flush()

    result = list_inbound_documents(db_session)
    row = next(item for item in result["rows"] if item["id"] == document.id)

    assert row["invoiceStatus"] == "部分开票"
    assert row["invoiceExpectedAmount"] == 100.0
    assert row["invoiceReceivedAmount"] == 80.0
    assert row["invoiceOutstandingAmount"] == 20.0
    assert row["invoiceCount"] == 1
    assert row["invoices"][0]["invoiceNo"] == "INV-INBOUND-001"
    assert row["invoices"][0]["matchedAmount"] == 80.0
