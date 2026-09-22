from datetime import datetime, timezone
from decimal import Decimal

from app.models.alibaba1688_import import Alibaba1688FileImport, Alibaba1688Order
from app.models.catalog import ProductSku
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem, JackyunPurchaseSettlement
from app.models.procurement_chain import ProcurementChainLink
from app.models.purchase import (
    ExternalPurchaseOrder,
    JackyunPurchaseOrder,
    JackyunPurchaseOrderLink,
    PurchaseAllocationItem,
    PurchaseInvoice,
    Supplier,
)
from app.models.tax import TaxInvoice, TaxInvoiceLink
from app.services import alibaba1688_import_service as import_service
from app.services import procurement_chain_service as service
from app.services import procurement_workbench_service as workbench_service
from app.services import purchase_service


def test_workbench_invoice_filter_waits_for_inbound():
    """待发票统计与筛选一致：未入库订单不能提前进入待发票。"""
    not_inbound = {
        "purchaseContentComplete": True,
        "allocations": [{"skuId": 1}],
        "purchaseOrders": [{}],
        "invoice": [],
        "inbound": [],
        "consumable": {},
        "invoiceOutstanding": Decimal("100"),
    }
    inbound = {
        **not_inbound,
        "inbound": [{"consumableUsageDecided": True}],
    }

    assert not workbench_service._matches_status(not_inbound, {}, "content", "invoice", set())
    assert workbench_service._matches_status(inbound, {}, "closeout", "invoice", set())


def test_workbench_list_fields_keeps_all_unique_inbound_documents():
    """主表必须保留一个采购单关联的全部入库单，不得只展示第一张。"""
    fields = workbench_service._supply_chain_list_fields({
        "allocations": [{"skuCode": "SKU-A", "goodsName": "测试商品", "quantity": "2"}],
        "orderItems": [],
        "inbound": [
            {"goodsdocNo": "RK-1", "warehouseName": "常州-示范仓"},
            {"goodsdocNo": "RK-2", "warehouseName": "常州-示范仓"},
            {"goodsdocNo": "RK-1", "warehouseName": "常州-示范仓"},
        ],
        "consumable": {},
        "logistics": {},
    })

    assert fields["jackyunInboundNo"] == "RK-1、RK-2"
    assert fields["warehouseName"] == "常州-示范仓"


def test_invoice_auto_match_supports_non_1688_workflow_order(db_session):
    """淘宝/拼多多等统一采购单按供应商档案税号参与进项发票自动匹配。"""
    db_session.add(Supplier(
        name="温州纸社包装有限公司",
        tax_no="91330383MAEWPXDP5H",
    ))
    order = ExternalPurchaseOrder(
        external_order_id="TAOBAO-INVOICE-001",
        platform="taobao",
        supplier_name="温州纸社包装有限公司",
        paid_amount=Decimal("800"),
        ordered_at=datetime(2025, 9, 28, tzinfo=timezone.utc),
    )
    old_invoice = TaxInvoice(
        invoice_key="pytest-non-1688-invoice-old",
        invoice_number="INV-NON-1688-OLD",
        direction="input",
        status="issued",
        seller_name="温州纸社包装有限公司",
        seller_tax_id="91330383MAEWPXDP5H",
        total_amount=Decimal("611"),
        issue_date=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    invoice = TaxInvoice(
        invoice_key="pytest-non-1688-invoice-001",
        invoice_number="INV-NON-1688-001",
        direction="input",
        status="issued",
        seller_name="温州纸社包装（发票抬头）",
        seller_tax_id="91330383MAEWPXDP5H",
        total_amount=Decimal("800"),
        issue_date=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )
    db_session.add_all([order, old_invoice, invoice])
    db_session.flush()
    db_session.add(TaxInvoiceLink(
        invoice_id=old_invoice.id,
        target_type="external_purchase_order",
        target_id=order.id,
        allocated_amount=Decimal("611"),
        match_method="rejected",
        confirmed=False,
    ))
    db_session.flush()

    result = service.ProcurementChainMatcher(db_session).run_match()

    link = db_session.query(TaxInvoiceLink).filter_by(
        target_type="external_purchase_order",
        target_id=order.id,
        invoice_id=invoice.id,
    ).one()
    assert link.confirmed is True
    assert link.allocated_amount == Decimal("800.0000")
    assert invoice.match_status == "matched"
    assert result["created"] >= 1

    states = workbench_service._step_states({
        "purchaseContentComplete": True,
        "consumable": {"received": True},
        "paidAmount": 800,
        "invoiceStatus": "done",
        "invoiceOutstanding": 0,
        "invoice": [{"id": invoice.id}],
    })
    assert states["closeout"]["detail"].endswith("已付款")


def test_invoice_auto_match_backfills_unique_purchase_supplier_tax_no(db_session):
    """有真实采购且发票卖方名称唯一全等时，自动补税号并继续完成发票匹配。"""
    supplier = Supplier(name="义乌市聚科注塑厂", tax_no="")
    order = ExternalPurchaseOrder(
        external_order_id="JUKETAX-BACKFILL-001",
        platform="taobao",
        supplier_name="义乌市聚科注塑厂",
        paid_amount=Decimal("800"),
        ordered_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    invoice = TaxInvoice(
        invoice_key="pytest-juke-tax-backfill",
        invoice_number="INV-JUKE-TAX-BACKFILL",
        direction="input",
        status="issued",
        seller_name="义乌市聚科注塑厂",
        seller_tax_id="91330782MA2JUKETAX",
        total_amount=Decimal("800"),
        issue_date=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )
    db_session.add_all([supplier, order, invoice])
    db_session.flush()

    result = service.ProcurementChainMatcher(db_session).run_match()

    db_session.refresh(supplier)
    assert supplier.tax_no == "91330782MA2JUKETAX"
    link = db_session.query(TaxInvoiceLink).filter_by(
        invoice_id=invoice.id,
        target_type="external_purchase_order",
        target_id=order.id,
    ).one()
    assert link.confirmed is True
    assert invoice.match_status == "matched"
    assert result["supplierTaxNoBackfilled"] == 1


def test_invoice_auto_match_does_not_backfill_ambiguous_blank_tax_profiles(db_session):
    """同名空税号主档不唯一时仍然不猜，避免把发票税号写到错误主体。"""
    db_session.add_all([
        Supplier(name="同名供应商", tax_no=""),
        Supplier(name="同名供应商", tax_no=""),
    ])
    order = ExternalPurchaseOrder(
        external_order_id="TAOBAO-INVOICE-AMBIGUOUS-TAX-PROFILE",
        platform="taobao",
        supplier_name="同名供应商",
        paid_amount=Decimal("800"),
        ordered_at=datetime(2025, 9, 28, tzinfo=timezone.utc),
    )
    invoice = TaxInvoice(
        invoice_key="pytest-invoice-ambiguous-tax-profile",
        invoice_number="INV-AMBIGUOUS-TAX-PROFILE",
        direction="input",
        status="issued",
        seller_name="同名供应商",
        seller_tax_id="91330000AMBIGUOUS",
        total_amount=Decimal("800"),
        issue_date=datetime(2025, 10, 1, tzinfo=timezone.utc),
    )
    db_session.add_all([order, invoice])
    db_session.flush()

    result = service.ProcurementChainMatcher(db_session).run_match()

    assert db_session.query(TaxInvoiceLink).filter_by(invoice_id=invoice.id).count() == 0
    assert db_session.query(Supplier).filter(Supplier.tax_no != "").count() == 0
    assert result["supplierProfileAmbiguous"] >= 1


def test_chain_row_merges_file_order_and_purchase_workflow(db_session):
    order_no = "CHAIN-TEST-001"
    file_import = Alibaba1688FileImport(
        original_name="chain-test.xlsx",
        stored_path="/tmp/chain-test.xlsx",
        sha256="chain-test-sha",
        lifecycle="active",
    )
    db_session.add(file_import)
    db_session.flush()
    file_order = Alibaba1688Order(
        external_order_id=order_no,
        seller_company_name="供应商甲",
        actual_payment=Decimal("100"),
        order_status="交易成功",
        raw_payload={"external_order_id": order_no},
        import_id=file_import.id,
    )
    workflow = ExternalPurchaseOrder(
        external_order_id=order_no,
        supplier_name="供应商甲",
        paid_amount=Decimal("100"),
        purchase_status="confirmed",
        logistics={"trackingNo": "SF-CHAIN-001", "company": "顺丰"},
    )
    db_session.add_all([file_order, workflow])
    db_session.flush()
    db_session.add(PurchaseAllocationItem(
        po_id=workflow.id,
        sku_id=1,
        sku_code="SKU-001",
        goods_name="测试商品",
        quantity=Decimal("2"),
        unit_price=Decimal("50"),
        amount=Decimal("100"),
    ))
    jpo = JackyunPurchaseOrder(
        jackyun_purch_id="JY-CHAIN-001",
        purch_no="CG-CHAIN-001",
        supplier_name="供应商甲",
        amount=Decimal("100"),
        status="已审核",
    )
    db_session.add(jpo)
    db_session.flush()
    db_session.add(JackyunPurchaseOrderLink(po_id=workflow.id, jackyun_po_id=jpo.id))
    db_session.flush()

    result = service.list_chain(db_session, limit=500)
    row = next(item for item in result["items"] if item["orderNo"] == order_no)

    assert result["total"] >= 1
    assert row["fileOrderId"] == file_order.id
    assert row["externalPoId"] == workflow.id
    assert row["purchaseContentComplete"] is True
    assert row["paidOn1688"] is True
    assert row["allocations"][0]["skuCode"] == "SKU-001"
    assert row["purchaseOrders"][0]["purchNo"] == "CG-CHAIN-001"

    workbench_rows = workbench_service.list_orders(db_session, page_size=100)
    workbench_row = next(
        item
        for group in workbench_rows["groups"]
        for item in group["items"]
        if item["orderNo"] == order_no
    )
    workbench_detail = workbench_service.workbench(db_session, file_order.id)

    assert workbench_row["orderId"] == file_order.id
    assert workbench_row["externalPoId"] == workflow.id
    assert workbench_row["purchaseStatus"] == "confirmed"
    assert workbench_row["productName"] == "测试商品"
    assert workbench_row["productQuantity"] == 2.0
    assert workbench_row["logisticsNo"] == "SF-CHAIN-001"
    assert workbench_row["logisticsCompany"] == "顺丰"
    assert workbench_detail is not None
    assert workbench_detail["order"]["externalPoId"] == workflow.id
    assert workbench_detail["order"]["purchaseStatus"] == "confirmed"
    assert workbench_detail["order"]["logistics"]["trackingNo"] == "SF-CHAIN-001"


def test_workflow_creation_backfills_sku_from_existing_inbound_link(db_session):
    """入库链早于工作流 PO 时，建立 PO 也应带出已匹配 SKU。"""
    order_no = "CHAIN-BACKFILL-SKU-001"
    file_import = Alibaba1688FileImport(
        original_name="chain-backfill.xlsx", stored_path="/tmp/chain-backfill.xlsx",
        sha256="chain-backfill-sha", lifecycle="active",
    )
    db_session.add(file_import)
    db_session.flush()
    source = Alibaba1688Order(external_order_id=order_no, import_id=file_import.id)
    sku = ProductSku(jackyun_sku_id="JY-BACKFILL-001", sku_code="SKU-BACKFILL-001", sku_name="回填测试 SKU")
    document = JackyunGoodsDocument(document_type="inbound", goodsdoc_no="RK-BACKFILL-001")
    db_session.add_all([source, sku, document])
    db_session.flush()
    db_session.add(JackyunGoodsDocumentItem(
        document_id=document.id, line_no=1, goods_no="SKU-BACKFILL-001",
        goods_name="回填测试 SKU", quantity=Decimal("2"), unit_price_tax=Decimal("10"),
        matched_sku_id=sku.id, match_status="auto",
    ))
    db_session.add(ProcurementChainLink(
        order_id=source.id, target_type="inbound", target_id=document.id,
        confirmed=True, match_method="manual",
    ))
    db_session.flush()

    workflow = purchase_service.create_external_po(
        db_session, external_order_id=order_no, supplier_name="回填供应商",
        order_amount="20", paid_amount="20",
    )

    allocations = db_session.query(PurchaseAllocationItem).filter_by(po_id=workflow.id).all()
    assert workflow.purchase_status == "confirmed"
    assert [(row.sku_id, row.sku_code, row.quantity, row.amount) for row in allocations] == [
        (sku.id, "SKU-BACKFILL-001", Decimal("2"), Decimal("20.0000")),
    ]


def test_active_import_reclaims_source_from_deleted_batch_without_reviving_deleted_rows(db_session):
    old_import = Alibaba1688FileImport(
        original_name="old.xlsx", stored_path="/tmp/old.xlsx", sha256="reclaim-old", lifecycle="deleted"
    )
    current_import = Alibaba1688FileImport(
        original_name="current.xlsx", stored_path="/tmp/current.xlsx", sha256="reclaim-current", lifecycle="active"
    )
    db_session.add_all([old_import, current_import])
    db_session.flush()
    active_order = Alibaba1688Order(
        external_order_id="RECLAIM-ACTIVE", import_id=old_import.id, seller_company_name="供应商甲"
    )
    deleted_order = Alibaba1688Order(
        external_order_id="RECLAIM-DELETED", import_id=old_import.id, row_status="deleted", seller_company_name="供应商乙"
    )
    db_session.add_all([active_order, deleted_order])
    db_session.flush()

    adopted, skipped = import_service._adopt_orders_from_deleted_imports(
        db_session,
        current_import,
        [
            {"external_order_id": "RECLAIM-ACTIVE", "seller_company_name": "供应商甲"},
            {"external_order_id": "RECLAIM-DELETED", "seller_company_name": "供应商乙"},
        ],
    )
    db_session.flush()

    assert adopted == 1
    assert skipped == 1
    assert active_order.import_id == current_import.id
    assert deleted_order.import_id == old_import.id
    assert deleted_order.row_status == "deleted"


def test_chain_hides_workflow_when_its_only_file_source_is_deleted(db_session):
    order_no = "DELETED-SOURCE-ONLY"
    deleted_import = Alibaba1688FileImport(
        original_name="deleted.xlsx", stored_path="/tmp/deleted.xlsx", sha256="deleted-source-only", lifecycle="deleted"
    )
    db_session.add(deleted_import)
    db_session.flush()
    db_session.add_all([
        Alibaba1688Order(external_order_id=order_no, import_id=deleted_import.id, seller_company_name="供应商甲"),
        ExternalPurchaseOrder(external_order_id=order_no, platform="1688", supplier_name="供应商甲"),
    ])
    db_session.flush()

    result = service.list_chain(db_session, limit=500)
    assert order_no not in {item["orderNo"] for item in result["items"]}


def test_supplier_name_alone_never_creates_inbound_link(db_session):
    file_import = Alibaba1688FileImport(
        original_name="manual-match-only.xlsx",
        stored_path="/tmp/manual-match-only.xlsx",
        sha256="manual-match-only-sha",
        lifecycle="active",
    )
    db_session.add(file_import)
    db_session.flush()
    order = Alibaba1688Order(
        external_order_id="MANUAL-MATCH-ONLY-001",
        seller_company_name="只同名不自动关联供应商",
        actual_payment=Decimal("100"),
        order_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        import_id=file_import.id,
    )
    document = JackyunGoodsDocument(
        document_type="inbound",
        goodsdoc_no="RK-MANUAL-MATCH-ONLY-001",
        supplier_name="只同名不自动关联供应商",
        total_amount=Decimal("999"),
        document_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    db_session.add_all([order, document])
    db_session.flush()

    service.ProcurementChainMatcher(db_session).run_match(auto_confirm=False)

    assert db_session.query(ProcurementChainLink).filter_by(
        order_id=order.id, target_type="inbound", target_id=document.id
    ).first() is None


def test_safe_match_creates_unconfirmed_suggestion(db_session):
    file_import = Alibaba1688FileImport(
        original_name="safe-suggestion.xlsx",
        stored_path="/tmp/safe-suggestion.xlsx",
        sha256="safe-suggestion-sha",
        lifecycle="active",
    )
    db_session.add(file_import)
    db_session.flush()
    order = Alibaba1688Order(
        external_order_id="SAFE-SUGGESTION-001",
        seller_company_name="保守候选供应商",
        actual_payment=Decimal("123.45"),
        order_time=datetime(2026, 2, 1, tzinfo=timezone.utc),
        import_id=file_import.id,
    )
    document = JackyunGoodsDocument(
        document_type="inbound",
        goodsdoc_no="RK-SAFE-SUGGESTION-001",
        supplier_name="保守候选供应商",
        total_amount=Decimal("123.45"),
        document_at=datetime(2026, 2, 3, tzinfo=timezone.utc),
    )
    db_session.add_all([order, document])
    db_session.flush()

    service.ProcurementChainMatcher(db_session).run_match(auto_confirm=False)
    link = db_session.query(ProcurementChainLink).filter_by(
        order_id=order.id, target_type="inbound", target_id=document.id
    ).one()

    assert link.confirmed is False
    assert link.match_method == "auto"
    assert "人工确认" in link.note


def test_auto_match_keeps_heuristic_inbound_pending(db_session):
    """自动运行时，只有入库单明确带订单号才能直接确认。"""
    file_import = Alibaba1688FileImport(
        original_name="auto-inbound-pending.xlsx",
        stored_path="/tmp/auto-inbound-pending.xlsx",
        sha256="auto-inbound-pending-sha",
        lifecycle="active",
    )
    db_session.add(file_import)
    db_session.flush()
    order = Alibaba1688Order(
        external_order_id="AUTO-INBOUND-PENDING-001",
        seller_company_name="自动候选供应商",
        actual_payment=Decimal("123.45"),
        order_time=datetime(2026, 2, 1, tzinfo=timezone.utc),
        import_id=file_import.id,
    )
    document = JackyunGoodsDocument(
        document_type="inbound",
        goodsdoc_no="RK-AUTO-INBOUND-PENDING-001",
        supplier_name="自动候选供应商",
        total_amount=Decimal("123.45"),
        document_at=datetime(2026, 2, 3, tzinfo=timezone.utc),
    )
    db_session.add_all([order, document])
    db_session.flush()

    result = service.ProcurementChainMatcher(db_session).run_match(auto_confirm=True)
    link = db_session.query(ProcurementChainLink).filter_by(
        order_id=order.id, target_type="inbound", target_id=document.id
    ).one()

    assert result["pendingInbound"] == 1
    assert result["requiresConfirmation"] is True
    assert link.confirmed is False
    assert link.match_method == "auto"


def test_heuristic_match_does_not_cross_link_explicit_inbound_owner(db_session):
    """已有文件/人工明确归属的入库单，不能再被启发式挂到另一张订单。"""
    file_import = Alibaba1688FileImport(
        original_name="authoritative-inbound-owner.xlsx",
        stored_path="/tmp/authoritative-inbound-owner.xlsx",
        sha256="authoritative-inbound-owner-sha",
        lifecycle="active",
    )
    db_session.add(file_import)
    db_session.flush()
    owner = Alibaba1688Order(
        external_order_id="AUTHORITATIVE-INBOUND-OWNER-001",
        seller_company_name="同供应商",
        actual_payment=Decimal("100"),
        order_time=datetime(2026, 3, 1, tzinfo=timezone.utc),
        import_id=file_import.id,
    )
    rival = Alibaba1688Order(
        external_order_id="AUTHORITATIVE-INBOUND-RIVAL-001",
        seller_company_name="同供应商",
        actual_payment=Decimal("100"),
        order_time=datetime(2026, 3, 9, tzinfo=timezone.utc),
        import_id=file_import.id,
    )
    document = JackyunGoodsDocument(
        document_type="inbound",
        goodsdoc_no="RK-AUTHORITATIVE-INBOUND-001",
        supplier_name="同供应商",
        total_amount=Decimal("100"),
        document_at=datetime(2026, 3, 10, tzinfo=timezone.utc),
    )
    db_session.add_all([owner, rival, document])
    db_session.flush()
    db_session.add(ProcurementChainLink(
        order_id=owner.id,
        target_type="inbound",
        target_id=document.id,
        match_method="file_import",
        confidence=Decimal("1"),
        confirmed=True,
    ))
    db_session.flush()

    result = service.ProcurementChainMatcher(db_session).run_match(auto_confirm=True)

    assert result["protectedInbound"] == 1
    assert db_session.query(ProcurementChainLink).filter_by(
        order_id=rival.id, target_type="inbound", target_id=document.id
    ).first() is None


def test_manual_inbound_link_can_be_replaced_and_removed(client, db_session):
    file_import = Alibaba1688FileImport(
        original_name="replace-link.xlsx",
        stored_path="/tmp/replace-link.xlsx",
        sha256="replace-link-sha",
        lifecycle="active",
    )
    db_session.add(file_import)
    db_session.flush()
    order = Alibaba1688Order(
        external_order_id="REPLACE-LINK-001",
        seller_company_name="人工更换供应商",
        actual_payment=Decimal("88"),
        order_time=datetime(2026, 3, 1, tzinfo=timezone.utc),
        import_id=file_import.id,
    )
    first = JackyunGoodsDocument(
        document_type="inbound", goodsdoc_no="RK-REPLACE-FIRST", supplier_name="人工更换供应商"
    )
    second = JackyunGoodsDocument(
        document_type="inbound", goodsdoc_no="RK-REPLACE-SECOND", supplier_name="人工更换供应商"
    )
    db_session.add_all([order, first, second])
    db_session.flush()

    created = client.post("/api/v1/procurement-chain/links", json={
        "order_id": order.id,
        "target_type": "inbound",
        "target_id": first.id,
        "note": "测试人工关联",
        "consumable_usage_enabled": False,
    })
    assert created.status_code == 200
    link_id = created.json()["id"]

    replaced = client.put(f"/api/v1/procurement-chain/links/{link_id}", json={
        "target_id": second.id,
        "note": "测试人工更换",
        "consumable_usage_enabled": False,
    })
    assert replaced.status_code == 200
    link = db_session.get(ProcurementChainLink, replaced.json()["id"])
    assert link is not None
    assert link.id != link_id
    assert link.target_id == second.id
    assert link.confirmed is True
    assert link.match_method == "manual"

    old_link = db_session.get(ProcurementChainLink, link_id)
    assert old_link is not None
    assert old_link.target_id == first.id
    assert old_link.confirmed is False
    assert old_link.match_method == "rejected"

    candidates = client.get("/api/v1/procurement-chain/candidates", params={
        "order_id": order.id,
        "target_type": "inbound",
        "q": "RK-REPLACE",
    })
    assert candidates.status_code == 200
    selected = next(item for item in candidates.json()["items"] if item["targetId"] == second.id)
    assert selected["currentlyLinked"] is True
    assert selected["linkId"] == link.id

    removed = client.delete(f"/api/v1/procurement-chain/links/{link.id}")
    assert removed.status_code == 200
    rejected = db_session.get(ProcurementChainLink, link.id)
    assert rejected is not None
    assert rejected.confirmed is False
    assert rejected.match_method == "rejected"

    pending = client.get("/api/v1/procurement-chain/pending")
    assert pending.status_code == 200
    assert link.id not in {item["linkId"] for item in pending.json()["items"]}


def test_workflow_only_order_can_link_a_real_settlement_without_id_collision(client, db_session):
    po = ExternalPurchaseOrder(
        external_order_id="MANUAL-WORKFLOW-SETTLEMENT-001", supplier_name="手工订单供应商",
        paid_amount=Decimal("88"), purchase_status="pending_refine",
    )
    settlement = JackyunPurchaseSettlement(
        settlement_no="SETTLE-WORKFLOW-001", supplier_name="手工订单供应商",
        settlement_amount=Decimal("88"), paid=Decimal("88"), status="已付款",
    )
    db_session.add_all([po, settlement])
    db_session.flush()

    created = client.post("/api/v1/procurement-chain/links", json={
        "order_id": -po.id, "target_type": "settlement", "target_id": settlement.id,
        "note": "手工订单关联真实结算单",
    })
    assert created.status_code == 200
    link = db_session.get(ProcurementChainLink, created.json()["id"])
    assert link is not None
    assert link.order_id is None
    assert link.external_po_id == po.id

    detail = workbench_service.workbench(db_session, -po.id)
    assert detail is not None
    assert detail["order"]["orderId"] == -po.id
    assert detail["detail"]["settlement"][0]["settlementNo"] == settlement.settlement_no


def test_register_invoice_is_atomic_and_rejects_missing_invoice_no(client, db_session):
    po = ExternalPurchaseOrder(
        external_order_id="ATOMIC-INVOICE-001", supplier_name="开票供应商",
        paid_amount=Decimal("100"), purchase_status="pending_refine",
    )
    db_session.add(po)
    db_session.flush()
    baseline = db_session.query(PurchaseInvoice).count()

    failed = client.post(f"/api/v1/purchase/orders/{po.id}/invoices", json={
        "invoice_no": "", "invoice_amount": "100",
    })
    assert failed.status_code == 400
    assert db_session.query(PurchaseInvoice).count() == baseline

    created = client.post(f"/api/v1/purchase/orders/{po.id}/invoices", json={
        "invoice_no": "INV-ATOMIC-001", "invoice_amount": "100", "allocated_amount": "100",
    })
    assert created.status_code == 200
    detail = client.get(f"/api/v1/purchase/orders/{po.id}")
    assert detail.status_code == 200
    assert detail.json()["invoices"][0]["invoiceNo"] == "INV-ATOMIC-001"


def test_unconfirmed_invoice_candidate_does_not_consume_order_coverage(db_session):
    """待确认候选不能占用采购单开票额度；只有 confirmed 分摊才是事实。"""
    db_session.add(Supplier(name="候选额度供应商", tax_no="91330000CANDIDATE01"))
    order = ExternalPurchaseOrder(
        external_order_id="CANDIDATE-COVERAGE-001",
        platform="taobao",
        supplier_name="候选额度供应商",
        paid_amount=Decimal("100"),
        ordered_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    pending_invoice = TaxInvoice(
        invoice_key="candidate-coverage-pending",
        invoice_number="INV-CANDIDATE-PENDING",
        direction="input",
        status="issued",
        seller_name="候选额度供应商",
        seller_tax_id="91330000CANDIDATE01",
        total_amount=Decimal("100"),
        issue_date=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )
    new_invoice = TaxInvoice(
        invoice_key="candidate-coverage-new",
        invoice_number="INV-CANDIDATE-NEW",
        direction="input",
        status="issued",
        seller_name="候选额度供应商",
        seller_tax_id="91330000CANDIDATE01",
        total_amount=Decimal("100"),
        issue_date=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )
    db_session.add_all([order, pending_invoice, new_invoice])
    db_session.flush()
    db_session.add(TaxInvoiceLink(
        invoice_id=pending_invoice.id,
        target_type="external_purchase_order",
        target_id=order.id,
        allocated_amount=Decimal("100"),
        match_method="auto",
        confirmed=False,
    ))
    db_session.flush()

    result = service.ProcurementChainMatcher(db_session).run_match(auto_confirm=True)

    new_link = db_session.query(TaxInvoiceLink).filter_by(
        invoice_id=new_invoice.id,
        target_type="external_purchase_order",
        target_id=order.id,
    ).one()
    assert new_link.confirmed is True
    assert new_link.allocated_amount == Decimal("100.0000")
    assert new_invoice.match_status == "matched"
    assert result["coverageUnknown"] == 0


def test_confirmed_invoice_link_without_allocation_blocks_further_auto_coverage(db_session):
    """历史已确认但无分摊金额的关联必须先人工复核，不能再继续自动叠加发票。"""
    db_session.add(Supplier(name="历史未知供应商", tax_no="91330000UNKNOWN001"))
    order = ExternalPurchaseOrder(
        external_order_id="UNKNOWN-COVERAGE-001",
        platform="taobao",
        supplier_name="历史未知供应商",
        paid_amount=Decimal("100"),
        ordered_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    legacy_invoice = TaxInvoice(
        invoice_key="unknown-coverage-legacy",
        invoice_number="INV-UNKNOWN-LEGACY",
        direction="input",
        status="issued",
        seller_name="历史未知供应商",
        seller_tax_id="91330000UNKNOWN001",
        total_amount=Decimal("100"),
        issue_date=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )
    new_invoice = TaxInvoice(
        invoice_key="unknown-coverage-new",
        invoice_number="INV-UNKNOWN-NEW",
        direction="input",
        status="issued",
        seller_name="历史未知供应商",
        seller_tax_id="91330000UNKNOWN001",
        total_amount=Decimal("100"),
        issue_date=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )
    db_session.add_all([order, legacy_invoice, new_invoice])
    db_session.flush()
    db_session.add(TaxInvoiceLink(
        invoice_id=legacy_invoice.id,
        target_type="external_purchase_order",
        target_id=order.id,
        allocated_amount=None,
        match_method="auto",
        confirmed=True,
    ))
    db_session.flush()

    result = service.ProcurementChainMatcher(db_session).run_match(auto_confirm=True)

    assert db_session.query(TaxInvoiceLink).filter_by(
        invoice_id=new_invoice.id,
        target_type="external_purchase_order",
        target_id=order.id,
    ).first() is None
    assert result["coverageUnknown"] >= 1

def test_purge_voided_invoice_links_preserves_rejected_history(db_session):
    invoice = TaxInvoice(
        invoice_key="PURGE-VOIDED-HISTORY",
        invoice_number="PURGE-VOIDED-HISTORY",
        direction="input",
        status="red",
        total_amount=Decimal("100"),
    )
    db_session.add(invoice)
    db_session.flush()
    link = TaxInvoiceLink(
        invoice_id=invoice.id,
        target_type="alibaba1688_order",
        target_id=999999,
        allocated_amount=Decimal("100"),
        match_method="manual",
        confirmed=True,
    )
    db_session.add(link)
    db_session.flush()
    link_id = link.id

    result = service.purge_voided_invoice_links(db_session, dry_run=False)
    assert result["removed"] >= 1
    kept = db_session.get(TaxInvoiceLink, link_id)
    assert kept is not None
    assert kept.confirmed is False
    assert kept.match_method == "rejected"
    assert "已停用" in kept.note

    again = service.purge_voided_invoice_links(db_session, dry_run=False)
    assert all(item["linkId"] != link_id for item in again["items"])



def test_auto_invoice_match_shares_coverage_across_1688_aliases(db_session):
    """工作流副本已有发票覆盖时，原始 1688 单自动匹配必须共用同一额度。"""
    file_import = Alibaba1688FileImport(
        original_name="alias-invoice-coverage.xlsx",
        stored_path="/tmp/alias-invoice-coverage.xlsx",
        sha256="alias-invoice-coverage-sha",
        lifecycle="active",
    )
    db_session.add(file_import)
    db_session.flush()
    order_no = "ALIAS-INVOICE-COVERAGE-001"
    raw_order = Alibaba1688Order(
        external_order_id=order_no,
        seller_company_name="Alias自动供应商",
        actual_payment=Decimal("100"),
        order_time=datetime(2026, 7, 1, tzinfo=timezone.utc),
        import_id=file_import.id,
    )
    workflow = ExternalPurchaseOrder(
        external_order_id=order_no,
        platform="1688",
        supplier_name="Alias自动供应商",
        paid_amount=Decimal("100"),
        order_amount=Decimal("100"),
        ordered_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    supplier = Supplier(
        name="Alias自动供应商",
        tax_no="91330000ALIASAUTO01",
    )
    existing_invoice = TaxInvoice(
        invoice_key="alias-auto-existing",
        invoice_number="INV-ALIAS-AUTO-EXISTING",
        direction="input",
        status="issued",
        seller_name="Alias自动供应商",
        seller_tax_id="91330000ALIASAUTO01",
        total_amount=Decimal("60"),
        issue_date=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )
    new_invoice = TaxInvoice(
        invoice_key="alias-auto-new",
        invoice_number="INV-ALIAS-AUTO-NEW",
        direction="input",
        status="issued",
        seller_name="Alias自动供应商",
        seller_tax_id="91330000ALIASAUTO01",
        total_amount=Decimal("100"),
        issue_date=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )
    db_session.add_all([raw_order, workflow, supplier, existing_invoice, new_invoice])
    db_session.flush()
    db_session.add(TaxInvoiceLink(
        invoice_id=existing_invoice.id,
        target_type="external_purchase_order",
        target_id=workflow.id,
        allocated_amount=Decimal("60"),
        match_method="manual",
        confirmed=True,
    ))
    db_session.commit()

    result = service.ProcurementChainMatcher(db_session).run_match(auto_confirm=True)

    assert result["overCovered"] >= 1
    assert db_session.query(TaxInvoiceLink).filter_by(
        invoice_id=new_invoice.id,
        target_type="alibaba1688_order",
        target_id=raw_order.id,
    ).first() is None


def test_auto_invoice_match_respects_rejected_link_on_1688_alias(db_session):
    """用户在工作流副本解除关联后，自动匹配不能从原始 1688 实体绕过拒绝。"""
    file_import = Alibaba1688FileImport(
        original_name="alias-rejected.xlsx",
        stored_path="/tmp/alias-rejected.xlsx",
        sha256="alias-rejected-sha",
        lifecycle="active",
    )
    db_session.add(file_import)
    db_session.flush()
    order_no = "ALIAS-REJECTED-001"
    raw_order = Alibaba1688Order(
        external_order_id=order_no,
        seller_company_name="Alias拒绝供应商",
        actual_payment=Decimal("100"),
        import_id=file_import.id,
    )
    workflow = ExternalPurchaseOrder(
        external_order_id=order_no,
        platform="1688",
        supplier_name="Alias拒绝供应商",
        paid_amount=Decimal("100"),
    )
    invoice = TaxInvoice(
        invoice_key="alias-rejected-invoice",
        invoice_number="INV-ALIAS-REJECTED",
        direction="input",
        status="issued",
        total_amount=Decimal("100"),
    )
    db_session.add_all([raw_order, workflow, invoice])
    db_session.flush()
    db_session.add(TaxInvoiceLink(
        invoice_id=invoice.id,
        target_type="external_purchase_order",
        target_id=workflow.id,
        allocated_amount=Decimal("100"),
        match_method="rejected",
        confirmed=False,
    ))
    db_session.flush()

    matcher = service.ProcurementChainMatcher(db_session)
    assert matcher._existing_invoice_link(
        "alibaba1688_order", raw_order.id, invoice.id
    ) is True


def test_chain_invoice_stage_uses_red_net_and_requires_full_coverage(db_session):
    """采购链批量预取也必须按红冲净额算；有票但未开够不能把第⑤环节标完成。"""
    token = "CHAIN-RED-NET"
    po = ExternalPurchaseOrder(
        external_order_id=token,
        platform="other",
        supplier_name="链路红冲供应商",
        paid_amount=Decimal("100"),
        order_amount=Decimal("100"),
        purchase_status="confirmed",
    )
    blue = TaxInvoice(
        invoice_key="chain-red-net-blue",
        invoice_number="CHAIN-RED-NET-BLUE",
        direction="input",
        status="issued",
        total_amount=Decimal("100"),
        raw={"是否正数发票": "是"},
    )
    red = TaxInvoice(
        invoice_key="chain-red-net-red",
        invoice_number="CHAIN-RED-NET-RED",
        direction="input",
        status="red",
        total_amount=Decimal("-40"),
        raw={
            "是否正数发票": "否",
            "备注": "被红冲蓝字发票号码：CHAIN-RED-NET-BLUE",
        },
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
    db_session.commit()

    payload = service.list_chain(db_session, limit=500)
    row = next(item for item in payload["items"] if item["orderNo"] == token)

    assert row["invoiceStatus"] == "partial"
    assert row["invoicedAmount"] == 60.0
    assert row["invoiceOutstanding"] == 40.0
    invoice_stage = next(stage for stage in row["stages"] if stage["key"] == "invoice")
    assert invoice_stage["done"] is False
    assert "部分开票" in invoice_stage["detail"]


def test_chain_invoice_review_state_blocks_invoice_stage_completion():
    row = {
        "orderNo": "REVIEW-ROW",
        "amount": 100.0,
        "allocations": [],
        "purchaseOrders": [],
        "inbound": [],
        "invoice": [{"amount": 50.0, "verified": False}],
        "settlement": [],
        "consumable": {},
        "paidOn1688": False,
        "invoiceStatus": "needs_review",
        "invoicedAmount": 0.0,
        "invoiceOutstanding": 100.0,
        "invoiceReviewReasons": ["一票多单红冲后原分摊超过有效蓝字净额，需人工重新分摊"],
    }

    stages = service._stage_states(row)
    invoice_stage = next(stage for stage in stages if stage["key"] == "invoice")

    assert invoice_stage["done"] is False
    assert "需复核" in invoice_stage["detail"]


def test_chain_verification_stage_requires_all_invoices_verified():
    row = {
        "orderNo": "VERIFY-ROW",
        "amount": 100.0,
        "allocations": [],
        "purchaseOrders": [],
        "inbound": [],
        "invoice": [
            {"amount": 50.0, "verified": True, "verifiedMonth": "2026-09"},
            {"amount": 50.0, "verified": False, "verifiedMonth": ""},
        ],
        "settlement": [],
        "consumable": {},
        "paidOn1688": False,
        "invoiceStatus": "done",
        "invoicedAmount": 100.0,
        "invoiceOutstanding": 0.0,
        "invoiceReviewReasons": [],
        "verified": False,
    }

    stages = service._stage_states(row)
    invoice_stage = next(stage for stage in stages if stage["key"] == "invoice")
    verified_stage = next(stage for stage in stages if stage["key"] == "verified")

    assert invoice_stage["done"] is True
    assert verified_stage["done"] is False
    assert "1/2" in verified_stage["detail"]
