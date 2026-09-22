from decimal import Decimal

from app.models.jackyun import JackyunGoodsDocument
from app.models.procurement_chain import ProcurementChainLink
from app.models.purchase import ExternalPurchaseOrder
from app.models.tax import TaxInvoice, TaxInvoiceImport, TaxInvoiceImportRecord, TaxInvoiceLink
from app.services import tax_invoice_service as service


def test_invoice_pool_exposes_purchase_and_inbound_trace(db_session):
    purchase = ExternalPurchaseOrder(
        external_order_id="POOL-PO-001",
        platform="1688",
        supplier_name="供应商甲",
        order_amount=Decimal("113"),
    )
    inbound = JackyunGoodsDocument(
        document_type="inbound",
        goodsdoc_no="POOL-RK-001",
        supplier_name="供应商甲",
        total_amount=Decimal("113"),
    )
    db_session.add_all([purchase, inbound])
    db_session.flush()
    db_session.add(ProcurementChainLink(
        external_po_id=purchase.id,
        target_type="inbound",
        target_id=inbound.id,
        match_method="manual",
        confirmed=True,
    ))
    invoice = TaxInvoice(
        invoice_key="POOL|INV-001",
        invoice_number="INV-001",
        direction="input",
        seller_name="供应商甲",
        total_amount=Decimal("113"),
        match_status="matched",
    )
    db_session.add(invoice)
    db_session.flush()
    db_session.add(TaxInvoiceLink(
        invoice_id=invoice.id,
        target_type="external_purchase_order",
        target_id=purchase.id,
        allocated_amount=Decimal("113"),
        match_method="manual",
        confirmed=True,
    ))
    db_session.flush()

    row = next(item for item in service.list_invoices(db_session, limit=500) if item["id"] == invoice.id)

    assert row["purchaseOrderNos"] == ["POOL-PO-001"]
    assert row["inboundNos"] == ["POOL-RK-001"]
    assert row["links"][0]["allocatedAmount"] == 113.0
    assert row["links"][0]["confirmed"] is True


def test_invoice_pool_marks_red_pair_and_excludes_voided_blue_amount(db_session):
    blue = TaxInvoice(
        invoice_key="POOL|BLUE-001",
        invoice_number="BLUE-001",
        direction="input",
        seller_name="供应商甲",
        total_amount=Decimal("100"),
        status="red",
        raw={"发票状态": "已红冲-全额", "是否正数发票": "是"},
    )
    red = TaxInvoice(
        invoice_key="POOL|RED-001",
        invoice_number="RED-001",
        direction="input",
        seller_name="供应商甲",
        total_amount=Decimal("-100"),
        status="red",
        raw={"发票状态": "正常", "是否正数发票": "否", "备注": "被红冲蓝字数电发票号码：BLUE-001 红字发票信息确认单编号：CONF-001"},
    )
    db_session.add_all([blue, red])
    db_session.flush()

    rows = {item["invoiceNumber"]: item for item in service.list_invoices(db_session, limit=500)}
    summary = service.summary(db_session)

    assert rows["BLUE-001"]["invoiceStatusLabel"] == "蓝字发票（已全额红冲）"
    assert rows["BLUE-001"]["redRelatedInvoiceNo"] == "RED-001"
    assert rows["BLUE-001"]["redStatus"] == "fully_red_offset"
    assert rows["RED-001"]["invoiceStatusLabel"] == "红字发票（冲销）"
    assert rows["RED-001"]["redRelatedInvoiceNo"] == "BLUE-001"
    assert rows["RED-001"]["redStatus"] == "red_invoice"
    assert rows["RED-001"]["redNoticeNo"] == "CONF-001"
    assert summary["inputTotalAmount"] == 0.0
    assert summary["excludedRedAmount"] == 0.0


def test_input_invoice_processing_status_and_inline_line_summary(db_session):
    batch = TaxInvoiceImport(
        original_name="processing-status.xlsx",
        stored_path="/tmp/processing-status.xlsx",
        sha256="processing-status-test-sha256",
        lifecycle="active",
    )
    db_session.add(batch)
    db_session.flush()
    invoice = TaxInvoice(
        invoice_key="POOL|PROCESSING-001",
        invoice_number="PROCESSING-001",
        direction="input",
        seller_name="供应商乙",
        total_amount=Decimal("113"),
        match_status="unmatched",
        source_import_id=batch.id,
        source_row_index=1,
    )
    db_session.add(invoice)
    db_session.flush()
    db_session.add(TaxInvoiceImportRecord(
        import_id=batch.id,
        row_index=1,
        invoice_id=invoice.id,
        recognition_status="recognized",
        payload={
            "货物或应税劳务、服务名称": "咖啡豆",
            "规格型号": "1kg/袋",
            "数量": "10",
            "不含税金额": "100",
            "税额": "13",
            "价税合计": "113",
        },
    ))
    db_session.flush()

    listed = next(item for item in service.list_invoices(db_session, direction="input") if item["id"] == invoice.id)
    assert listed["processingStatus"] == "pending"
    assert listed["lineItemCount"] == 1
    assert listed["lineItems"][0]["goodsName"] == "咖啡豆"

    service.set_processing_status(db_session, invoice.id, "required", actor="pytest")
    required = service.list_invoices(db_session, direction="input", processing_status="required")
    assert [item["id"] for item in required] == [invoice.id]
