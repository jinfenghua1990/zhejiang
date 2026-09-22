from decimal import Decimal

from app.models.purchase import ExternalPurchaseOrder
from app.models.tax import TaxInvoice, TaxInvoiceLink
from app.services import (
    alibaba1688_import_service,
    inbound_allocation_seed,
    platform_purchase_guard,
    procurement_chain_service,
    tax_invoice_service,
)


def test_install_platform_purchase_guards_no_longer_replaces_runtime_functions():
    source_pairs_before = procurement_chain_service._source_pairs
    sync_before = alibaba1688_import_service._sync_purchase_workflow_order
    po_from_link_before = inbound_allocation_seed._po_from_link
    collect_before = inbound_allocation_seed.collect_linked_doc_ids_for_po
    tax_auto_before = tax_invoice_service._auto_link

    platform_purchase_guard.install_platform_purchase_guards()

    assert procurement_chain_service._source_pairs is source_pairs_before
    assert alibaba1688_import_service._sync_purchase_workflow_order is sync_before
    assert inbound_allocation_seed._po_from_link is po_from_link_before
    assert inbound_allocation_seed.collect_linked_doc_ids_for_po is collect_before
    assert tax_invoice_service._auto_link is tax_auto_before


def test_1688_sync_never_overwrites_other_channel_same_order_number(db_session):
    pdd = ExternalPurchaseOrder(
        external_order_id="SAME-ORDER-001",
        platform="pdd",
        supplier_name="拼多多原供应商",
        order_amount=Decimal("88"),
        paid_amount=Decimal("88"),
    )
    db_session.add(pdd)
    db_session.flush()

    changed = alibaba1688_import_service._sync_purchase_workflow_order(
        db_session,
        {
            "external_order_id": "SAME-ORDER-001",
            "seller_company_name": "1688供应商",
            "actual_payment": "100",
            "goods_total": "100",
        },
    )
    assert changed is True
    db_session.flush()

    same_no = (
        db_session.query(ExternalPurchaseOrder)
        .filter_by(external_order_id="SAME-ORDER-001")
        .order_by(ExternalPurchaseOrder.id)
        .all()
    )
    assert len(same_no) == 2
    by_platform = {row.platform: row for row in same_no}
    assert by_platform["pdd"].supplier_name == "拼多多原供应商"
    assert by_platform["pdd"].paid_amount == Decimal("88")
    assert by_platform["1688"].supplier_name == "1688供应商"
    assert by_platform["1688"].paid_amount == Decimal("100")


def test_tax_source_ref_with_multi_channel_same_order_requires_review(db_session):
    order_no = "TAX-SAME-ORDER-001"
    db_session.add_all([
        ExternalPurchaseOrder(
            external_order_id=order_no,
            platform="1688",
            supplier_name="供应商A",
            paid_amount=Decimal("100"),
        ),
        ExternalPurchaseOrder(
            external_order_id=order_no,
            platform="pdd",
            supplier_name="供应商A",
            paid_amount=Decimal("100"),
        ),
    ])
    invoice = TaxInvoice(
        invoice_key="TAX-MULTI-CHANNEL-INV",
        direction="input",
        invoice_number="TAX-MULTI-CHANNEL-INV",
        total_amount=Decimal("100"),
        match_status="unmatched",
    )
    db_session.add(invoice)
    db_session.flush()

    matched = tax_invoice_service._auto_link(db_session, invoice, order_no, "input")

    assert matched is False
    assert invoice.match_status == "needs_review"
    assert "多个采购渠道" in invoice.match_note
    assert db_session.query(TaxInvoiceLink).filter_by(invoice_id=invoice.id).count() == 0
