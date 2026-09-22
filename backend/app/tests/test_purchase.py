from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.catalog import Warehouse
from app.models.purchase import (
    ExternalPurchaseOrder,
    InboundLink,
    JackyunPurchaseOrder,
    JackyunPurchaseOrderLink,
    PurchaseInvoice,
    PurchaseInvoiceLink,
)
from app.models.tax import TaxInvoice, TaxInvoiceLink
from app.services import purchase_invoice_truth_service as invoice_truth
from app.services.purchase_service import (
    _has_actual_inbound,
    create_external_po,
    create_invoice,
    derive_invoice_status,
    register_order_invoice,
    set_invoice_status,
    unlink_jackyun_po_link,
    update_external_po,
    validate_transition,
)


def test_forward_transition_valid():
    assert validate_transition("pending_refine", "confirmed")
    assert validate_transition("confirmed", "jackyun_linked")
    assert validate_transition("arrived", "inbound")


def test_backward_or_skip_transition_invalid():
    assert not validate_transition("confirmed", "pending_refine")   # 回退
    assert not validate_transition("pending_refine", "shipped")     # 跳步
    assert not validate_transition("done", "done")                  # 原地
    assert not validate_transition("whatever", "confirmed")         # 非法态


def test_invoice_status_full_when_covered():
    assert derive_invoice_status("50000", "50000", "applied") == "full"
    assert derive_invoice_status("50000", "52000", "applied") == "full"


def test_invoice_status_partial():
    assert derive_invoice_status("50000", "20000", "applied") == "partial"


def test_invoice_status_none_kept_when_no_link():
    assert derive_invoice_status("50000", "0", "none") == "none"
    assert derive_invoice_status("50000", "0", "unverified") == "unverified"


def test_invoice_status_defaults_unverified():
    assert derive_invoice_status("50000", "0", "weird") == "unverified"


def test_zero_paid_with_links_is_partial():
    assert derive_invoice_status("0", "100", "none") == "partial"


def test_actual_inbound_is_detected_from_legacy_link(db_session):
    po = ExternalPurchaseOrder(
        external_order_id="PYTEST-LEGACY-INBOUND",
        platform="taobao",
        purchase_status="jackyun_linked",
    )
    db_session.add(po)
    db_session.flush()
    assert not _has_actual_inbound(db_session, po)

    db_session.add(InboundLink(po_id=po.id, goodsdoc_no="RK-PYTEST-001"))
    db_session.flush()
    assert _has_actual_inbound(db_session, po)


def test_purchase_order_target_warehouse_is_saved_and_can_be_cleared(db_session):
    warehouse = Warehouse(code="WH-PURCHASE-TEST", name="采购测试仓", purpose="goods", status="active")
    db_session.add(warehouse)
    db_session.flush()
    po = create_external_po(
        db_session,
        external_order_id="PYTEST-TARGET-WAREHOUSE",
        supplier_name="测试供应商",
        warehouse_id=warehouse.id,
    )
    assert po.warehouse_id == warehouse.id

    update_external_po(db_session, po, warehouse_id=None)
    assert po.warehouse_id is None


def test_purchase_order_target_warehouse_rejects_inactive_warehouse(db_session):
    warehouse = Warehouse(code="WH-PURCHASE-INACTIVE", name="停用测试仓", purpose="goods", status="inactive")
    db_session.add(warehouse)
    db_session.flush()
    po = ExternalPurchaseOrder(external_order_id="PYTEST-INACTIVE-WAREHOUSE")
    db_session.add(po)
    db_session.flush()

    with pytest.raises(ValueError, match="不存在或已停用"):
        update_external_po(db_session, po, warehouse_id=warehouse.id)


def test_local_purchase_order_number_is_the_editable_system_key(db_session):
    po = ExternalPurchaseOrder(
        external_order_id="PDD-TEMP-ORDER",
        platform="pdd",
        supplier_name="本地采购供应商",
    )
    db_session.add(po)
    db_session.flush()

    update_external_po(db_session, po, external_order_id="PDD-LOCAL-001")

    assert po.external_order_id == "PDD-LOCAL-001"


def test_local_purchase_order_number_cannot_duplicate_same_channel(db_session):
    first = ExternalPurchaseOrder(external_order_id="PDD-LOCAL-DUP", platform="pdd")
    second = ExternalPurchaseOrder(external_order_id="PDD-LOCAL-OTHER", platform="pdd")
    db_session.add_all([first, second])
    db_session.flush()

    with pytest.raises(ValueError, match="当前渠道已存在"):
        update_external_po(db_session, second, external_order_id=first.external_order_id)


def test_formal_purchase_creation_promotes_reference_only_order(db_session):
    reference = ExternalPurchaseOrder(
        external_order_id="PDD-REFERENCE-001",
        platform="pdd",
        supplier_name="入库文件供应商",
        raw={"referenceOnly": True, "source": "jackyun_inbound_apply"},
    )
    db_session.add(reference)
    db_session.flush()

    promoted = create_external_po(
        db_session,
        external_order_id="PDD-REFERENCE-001",
        platform="pdd",
        supplier_name="正式采购供应商",
        title="正式采购单",
        order_amount="1800",
        paid_amount="1800",
    )

    assert promoted.id == reference.id
    assert promoted.raw["referenceOnly"] is False
    assert promoted.supplier_name == "正式采购供应商"
    assert promoted.order_amount == Decimal("1800")



def test_unlinking_one_of_multiple_jackyun_links_keeps_linked_status(db_session):
    po = ExternalPurchaseOrder(
        external_order_id="PYTEST-JY-SPLIT-PO",
        platform="1688",
        purchase_status="jackyun_linked",
    )
    first = JackyunPurchaseOrder(
        jackyun_purch_id="PYTEST-JY-SPLIT-1",
        purch_no="PYTEST-JY-SPLIT-1",
    )
    second = JackyunPurchaseOrder(
        jackyun_purch_id="PYTEST-JY-SPLIT-2",
        purch_no="PYTEST-JY-SPLIT-2",
    )
    db_session.add_all([po, first, second])
    db_session.flush()
    first_link = JackyunPurchaseOrderLink(
        po_id=po.id, jackyun_po_id=first.id,
        relation_kind="split", alloc_amount=Decimal("60"),
    )
    second_link = JackyunPurchaseOrderLink(
        po_id=po.id, jackyun_po_id=second.id,
        relation_kind="split", alloc_amount=Decimal("40"),
    )
    db_session.add_all([first_link, second_link])
    db_session.commit()

    result = unlink_jackyun_po_link(db_session, po, first_link, actor="pytest")
    db_session.refresh(po)

    assert result["remainingLinks"] == 1
    assert result["statusReverted"] is False
    assert po.purchase_status == "jackyun_linked"
    assert db_session.query(JackyunPurchaseOrderLink).filter_by(po_id=po.id).count() == 1


def test_unlinking_last_jackyun_link_reverts_to_confirmed(db_session):
    po = ExternalPurchaseOrder(
        external_order_id="PYTEST-JY-LAST-PO",
        platform="1688",
        purchase_status="jackyun_linked",
    )
    jpo = JackyunPurchaseOrder(
        jackyun_purch_id="PYTEST-JY-LAST",
        purch_no="PYTEST-JY-LAST",
    )
    db_session.add_all([po, jpo])
    db_session.flush()
    link = JackyunPurchaseOrderLink(po_id=po.id, jackyun_po_id=jpo.id)
    db_session.add(link)
    db_session.commit()

    result = unlink_jackyun_po_link(db_session, po, link, actor="pytest")
    db_session.refresh(po)

    assert result["remainingLinks"] == 0
    assert result["statusReverted"] is True
    assert po.purchase_status == "confirmed"


def test_late_purchase_state_cannot_drop_jackyun_link_directly(db_session):
    po = ExternalPurchaseOrder(
        external_order_id="PYTEST-JY-LATE-PO",
        platform="1688",
        purchase_status="inbound",
    )
    jpo = JackyunPurchaseOrder(
        jackyun_purch_id="PYTEST-JY-LATE",
        purch_no="PYTEST-JY-LATE",
    )
    db_session.add_all([po, jpo])
    db_session.flush()
    link = JackyunPurchaseOrderLink(po_id=po.id, jackyun_po_id=jpo.id)
    db_session.add(link)
    db_session.commit()

    with pytest.raises(ValueError, match="不允许直接解除"):
        unlink_jackyun_po_link(db_session, po, link, actor="pytest")

    assert db_session.get(JackyunPurchaseOrderLink, link.id) is not None
    assert po.purchase_status == "inbound"


def test_invoice_truth_ignores_stale_cache_and_unconfirmed_candidate(db_session):
    token = uuid4().hex[:10]
    po = ExternalPurchaseOrder(
        external_order_id=f"TRUTH-PENDING-{token}",
        platform="other",
        paid_amount=Decimal("100"),
        order_amount=Decimal("100"),
        invoice_status="full",  # 历史缓存故意写错，不能影响最终结论
    )
    invoice = TaxInvoice(
        invoice_key=f"truth-pending-{token}",
        invoice_number=f"INV-TRUTH-PENDING-{token}",
        direction="input",
        status="issued",
        total_amount=Decimal("100"),
    )
    db_session.add_all([po, invoice])
    db_session.flush()
    db_session.add(TaxInvoiceLink(
        invoice_id=invoice.id,
        target_type="external_purchase_order",
        target_id=po.id,
        allocated_amount=Decimal("100"),
        match_method="auto",
        confirmed=False,
    ))
    db_session.flush()

    result = invoice_truth.purchase_invoice_truth(db_session, external=po)

    assert result["status"] == "pending"
    assert result["invoicedAmount"] == Decimal("0")
    assert result["outstandingAmount"] == Decimal("100")
    assert result["taxInvoiceCount"] == 0


def test_official_tax_invoice_replaces_same_number_legacy_manual_fact(db_session):
    token = uuid4().hex[:10]
    invoice_no = f"INV-DUAL-{token}"
    po = ExternalPurchaseOrder(
        external_order_id=f"PO-DUAL-{token}",
        platform="other",
        paid_amount=Decimal("100"),
        order_amount=Decimal("100"),
    )
    legacy = PurchaseInvoice(
        invoice_no=invoice_no,
        invoice_amount=Decimal("100"),
        supplier_name="双事实供应商",
    )
    official = TaxInvoice(
        invoice_key=f"|{invoice_no}",
        invoice_number=invoice_no,
        direction="input",
        status="issued",
        total_amount=Decimal("100"),
    )
    db_session.add_all([po, legacy, official])
    db_session.flush()
    db_session.add_all([
        PurchaseInvoiceLink(
            invoice_id=legacy.id,
            po_id=po.id,
            allocated_amount=Decimal("100"),
        ),
        TaxInvoiceLink(
            invoice_id=official.id,
            target_type="external_purchase_order",
            target_id=po.id,
            allocated_amount=Decimal("100"),
            match_method="manual",
            confirmed=True,
        ),
    ])
    db_session.flush()

    result = invoice_truth.purchase_invoice_truth(db_session, external=po)

    assert result["status"] == "done"
    assert result["invoicedAmount"] == Decimal("100")
    assert result["taxInvoiceCount"] == 1
    assert result["legacyInvoiceCount"] == 0
    assert [row["invoiceKind"] for row in result["entries"]] == ["tax"]


def test_single_order_partial_red_uses_blue_invoice_net_amount(db_session):
    token = uuid4().hex[:10]
    blue_no = f"BLUE-TRUTH-{token}"
    po = ExternalPurchaseOrder(
        external_order_id=f"PO-RED-NET-{token}",
        platform="other",
        paid_amount=Decimal("100"),
        order_amount=Decimal("100"),
    )
    blue = TaxInvoice(
        invoice_key=f"blue-truth-{token}",
        invoice_number=blue_no,
        direction="input",
        status="issued",
        issue_date=datetime(2026, 9, 1, tzinfo=timezone.utc),
        total_amount=Decimal("100"),
        raw={"是否正数发票": "是"},
    )
    red = TaxInvoice(
        invoice_key=f"red-truth-{token}",
        invoice_number=f"RED-TRUTH-{token}",
        direction="input",
        status="red",
        issue_date=datetime(2026, 9, 2, tzinfo=timezone.utc),
        total_amount=Decimal("-40"),
        raw={"是否正数发票": "否", "备注": f"被红冲蓝字发票号码：{blue_no}"},
    )
    db_session.add_all([po, blue, red])
    db_session.flush()
    db_session.add(TaxInvoiceLink(
        invoice_id=blue.id,
        target_type="external_purchase_order",
        target_id=po.id,
        allocated_amount=Decimal("100"),
        match_method="manual",
        confirmed=True,
    ))
    db_session.flush()

    result = invoice_truth.purchase_invoice_truth(db_session, external=po)

    assert result["status"] == "partial"
    assert result["invoicedAmount"] == Decimal("60")
    assert result["outstandingAmount"] == Decimal("40")
    assert result["needsReview"] is False
    assert result["entries"][0]["redAdjusted"] is True
    assert Decimal(str(result["entries"][0]["amount"])) == Decimal("60")


def test_multi_order_partial_red_requires_reallocation_review(db_session):
    token = uuid4().hex[:10]
    blue_no = f"BLUE-SPLIT-{token}"
    po_a = ExternalPurchaseOrder(
        external_order_id=f"PO-SPLIT-A-{token}",
        platform="other",
        paid_amount=Decimal("60"),
        order_amount=Decimal("60"),
    )
    po_b = ExternalPurchaseOrder(
        external_order_id=f"PO-SPLIT-B-{token}",
        platform="other",
        paid_amount=Decimal("40"),
        order_amount=Decimal("40"),
    )
    blue = TaxInvoice(
        invoice_key=f"blue-split-{token}",
        invoice_number=blue_no,
        direction="input",
        status="issued",
        total_amount=Decimal("100"),
        raw={"是否正数发票": "是"},
    )
    red = TaxInvoice(
        invoice_key=f"red-split-{token}",
        invoice_number=f"RED-SPLIT-{token}",
        direction="input",
        status="red",
        total_amount=Decimal("-30"),
        raw={"是否正数发票": "否", "备注": f"被红冲蓝字发票号码：{blue_no}"},
    )
    db_session.add_all([po_a, po_b, blue, red])
    db_session.flush()
    db_session.add_all([
        TaxInvoiceLink(
            invoice_id=blue.id,
            target_type="external_purchase_order",
            target_id=po_a.id,
            allocated_amount=Decimal("60"),
            match_method="manual",
            confirmed=True,
        ),
        TaxInvoiceLink(
            invoice_id=blue.id,
            target_type="external_purchase_order",
            target_id=po_b.id,
            allocated_amount=Decimal("40"),
            match_method="manual",
            confirmed=True,
        ),
    ])
    db_session.flush()

    result = invoice_truth.purchase_invoice_truth(db_session, external=po_a)

    assert result["status"] == "needs_review"
    assert result["needsReview"] is True
    assert result["invoicedAmount"] == Decimal("0")
    assert any("一票多单红冲" in reason for reason in result["reviewReasons"])


def test_legacy_manual_invoice_cannot_duplicate_official_tax_invoice(db_session):
    token = uuid4().hex[:10]
    invoice_no = f"INV-OFFICIAL-{token}"
    official = TaxInvoice(
        invoice_key=f"|{invoice_no}",
        invoice_number=invoice_no,
        direction="input",
        status="issued",
        total_amount=Decimal("88"),
    )
    db_session.add(official)
    db_session.flush()

    with pytest.raises(ValueError, match="正式税务发票台账"):
        create_invoice(
            db_session,
            invoice_no=invoice_no,
            invoice_amount="88",
            actor="pytest",
        )


def test_legacy_invoice_status_is_read_only_derived_fact(db_session):
    po = ExternalPurchaseOrder(
        external_order_id=f"PO-INVOICE-STATUS-{uuid4().hex[:10]}",
        platform="other",
    )
    db_session.add(po)
    db_session.flush()

    with pytest.raises(ValueError, match="自动计算"):
        set_invoice_status(db_session, po, "full", actor="pytest")


def test_canonical_invoice_truth_flags_order_overcoverage(db_session):
    token = uuid4().hex[:10]
    po = ExternalPurchaseOrder(
        external_order_id=f"PO-OVER-COVER-{token}",
        platform="other",
        paid_amount=Decimal("100"),
        order_amount=Decimal("100"),
    )
    official = TaxInvoice(
        invoice_key=f"official-over-{token}",
        invoice_number=f"INV-OFFICIAL-OVER-{token}",
        direction="input",
        status="issued",
        total_amount=Decimal("80"),
    )
    legacy = PurchaseInvoice(
        invoice_no=f"INV-LEGACY-OVER-{token}",
        invoice_amount=Decimal("30"),
    )
    db_session.add_all([po, official, legacy])
    db_session.flush()
    db_session.add_all([
        TaxInvoiceLink(
            invoice_id=official.id,
            target_type="external_purchase_order",
            target_id=po.id,
            allocated_amount=Decimal("80"),
            match_method="manual",
            confirmed=True,
        ),
        PurchaseInvoiceLink(
            invoice_id=legacy.id,
            po_id=po.id,
            allocated_amount=Decimal("30"),
        ),
    ])
    db_session.flush()

    result = invoice_truth.purchase_invoice_truth(db_session, external=po)

    assert result["status"] == "needs_review"
    assert result["invoicedAmount"] == Decimal("110")
    assert result["outstandingAmount"] == Decimal("0")
    assert any("超过应开票金额" in reason for reason in result["reviewReasons"])


def test_legacy_manual_write_cannot_ignore_existing_official_tax_coverage(db_session):
    token = uuid4().hex[:10]
    po = ExternalPurchaseOrder(
        external_order_id=f"PO-WRITE-COVER-{token}",
        platform="other",
        paid_amount=Decimal("100"),
        order_amount=Decimal("100"),
    )
    official = TaxInvoice(
        invoice_key=f"official-write-{token}",
        invoice_number=f"INV-OFFICIAL-WRITE-{token}",
        direction="input",
        status="issued",
        total_amount=Decimal("80"),
    )
    db_session.add_all([po, official])
    db_session.flush()
    db_session.add(TaxInvoiceLink(
        invoice_id=official.id,
        target_type="external_purchase_order",
        target_id=po.id,
        allocated_amount=Decimal("80"),
        match_method="manual",
        confirmed=True,
    ))
    db_session.flush()

    with pytest.raises(ValueError, match="超过采购应开票金额"):
        register_order_invoice(
            db_session,
            po,
            invoice_no=f"INV-LEGACY-WRITE-{token}",
            invoice_amount="30",
            allocated_amount="30",
            actor="pytest",
        )
