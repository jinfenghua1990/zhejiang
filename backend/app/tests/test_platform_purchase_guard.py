from decimal import Decimal

from app.models.alibaba1688_import import Alibaba1688FileImport, Alibaba1688Order
from app.models.purchase import ExternalPurchaseOrder
from app.models.tax import TaxInvoice
from app.services.platform_purchase_guard import (
    platform_source_pairs,
    sync_1688_purchase_workflow_order,
    tax_auto_link_platform_safe,
)


def test_source_pairs_do_not_merge_taobao_same_number_into_1688(db_session):
    batch = Alibaba1688FileImport(
        original_name="same-no.xlsx",
        stored_path="/tmp/same-no.xlsx",
        sha256="same-no-platform-guard",
        lifecycle="active",
    )
    db_session.add(batch)
    db_session.flush()
    source = Alibaba1688Order(
        external_order_id="PLATFORM-SAME-001",
        seller_company_name="1688供应商",
        actual_payment=Decimal("100"),
        import_id=batch.id,
    )
    workflow_1688 = ExternalPurchaseOrder(
        external_order_id="PLATFORM-SAME-001",
        platform="1688",
        supplier_name="1688供应商",
        paid_amount=Decimal("100"),
    )
    workflow_taobao = ExternalPurchaseOrder(
        external_order_id="PLATFORM-SAME-001",
        platform="taobao",
        supplier_name="淘宝供应商",
        paid_amount=Decimal("80"),
    )
    db_session.add_all([source, workflow_1688, workflow_taobao])
    db_session.flush()

    pairs = platform_source_pairs(db_session)
    paired = [(source_row, workflow) for source_row, workflow in pairs if source_row is not None]
    assert len(paired) == 1
    assert paired[0][0].id == source.id
    assert paired[0][1].id == workflow_1688.id
    assert any(source_row is None and workflow.id == workflow_taobao.id for source_row, workflow in pairs)


def test_reference_only_purchase_is_hidden_from_normal_platform_pairs(db_session):
    reference = ExternalPurchaseOrder(
        external_order_id="20260501001",
        platform="pdd",
        supplier_name="拼多多临时采购",
        raw={"referenceOnly": True, "source": "jackyun_inbound_apply"},
    )
    normal = ExternalPurchaseOrder(
        external_order_id="NORMAL-PDD-001",
        platform="pdd",
        supplier_name="正常拼多多采购",
    )
    db_session.add_all([reference, normal])
    db_session.flush()

    pairs = platform_source_pairs(db_session)
    ids = {workflow.id for _, workflow in pairs if workflow is not None}
    assert reference.id not in ids
    assert normal.id in ids


def test_1688_sync_updates_only_1688_same_number(db_session):
    workflow_1688 = ExternalPurchaseOrder(
        external_order_id="SYNC-SAME-001",
        platform="1688",
        supplier_name="旧1688名称",
        paid_amount=Decimal("100"),
    )
    workflow_pdd = ExternalPurchaseOrder(
        external_order_id="SYNC-SAME-001",
        platform="pdd",
        supplier_name="拼多多名称",
        paid_amount=Decimal("88"),
    )
    db_session.add_all([workflow_1688, workflow_pdd])
    db_session.flush()

    changed = sync_1688_purchase_workflow_order(
        db_session,
        {
            "external_order_id": "SYNC-SAME-001",
            "seller_company_name": "新1688名称",
            "actual_payment": "120",
        },
    )
    db_session.flush()

    assert changed is True
    assert workflow_1688.supplier_name == "新1688名称"
    assert workflow_1688.paid_amount == Decimal("120")
    assert workflow_pdd.supplier_name == "拼多多名称"
    assert workflow_pdd.paid_amount == Decimal("88")


def test_tax_order_number_with_multiple_platform_matches_requires_review(db_session):
    first = ExternalPurchaseOrder(
        external_order_id="TAX-SAME-001",
        platform="1688",
        supplier_name="供应商甲",
    )
    second = ExternalPurchaseOrder(
        external_order_id="TAX-SAME-001",
        platform="taobao",
        supplier_name="供应商乙",
    )
    invoice = TaxInvoice(
        invoice_key="|TAX-GUARD-INV",
        invoice_number="TAX-GUARD-INV",
        direction="input",
        total_amount=Decimal("100"),
    )
    db_session.add_all([first, second, invoice])
    db_session.flush()

    matched = tax_auto_link_platform_safe(
        db_session,
        invoice,
        "TAX-SAME-001",
        "input",
    )
    assert matched is False
    assert invoice.match_status == "needs_review"
    assert "多个采购渠道" in invoice.match_note
