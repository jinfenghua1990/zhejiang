"""入库单明细反填的归属排他守卫。

规则：入库单明细行一经反填给某个采购单，就不能再反填到其他采购单
（拆分单/共用入库单场景）。
- 新行带 source_item_id → 行级排他（同 doc 同 SKU 多行时各行可归属不同采购单）；
- 历史行无 source_item_id → 退化为 (doc_id, sku_id) 排他（保守）。
"""

from __future__ import annotations

from decimal import Decimal

from app.models.catalog import ProductSku
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.models.ops import ExceptionRecord
from app.models.procurement_chain import ProcurementChainLink
from app.models.purchase import ExternalPurchaseOrder, InboundLink, PurchaseAllocationItem
from app.services.inbound_allocation_seed import collect_linked_doc_ids_for_po, seed_allocations_for_po
from app.services.procurement_chain_service import ProcurementChainMatcher


def _mk_po(db, external_order_id: str, platform: str = "taobao") -> ExternalPurchaseOrder:
    po = ExternalPurchaseOrder(
        external_order_id=external_order_id, platform=platform,
        supplier_name="测试供应商", purchase_status="pending_refine",
    )
    db.add(po)
    db.flush()
    return po


def _mk_sku(db, code: str) -> ProductSku:
    sku = ProductSku(
        jackyun_sku_id=f"J-{code}", sku_code=code, sku_name=f"测试SKU {code}",
        cost_mode="fixed",
    )
    db.add(sku)
    db.flush()
    return sku


def _mk_doc(db, no: str, lines: list[tuple[int, int, str, str]]) -> JackyunGoodsDocument:
    """lines: [(line_no, qty, goods_no, price)]；matched_sku_id 由调用方按 goods_no 回填。"""
    doc = JackyunGoodsDocument(
        document_type="inbound", goodsdoc_no=no, supplier_name="测试仓库供应商",
        warehouse_code="WH", warehouse_name="测试仓", company_name="测试公司",
    )
    db.add(doc)
    db.flush()
    return doc


def _mk_item(db, doc_id: int, line_no: int, goods_no: str, qty: str, price: str | None,
             sku_id: int | None) -> JackyunGoodsDocumentItem:
    it = JackyunGoodsDocumentItem(
        document_id=doc_id, line_no=line_no, goods_no=goods_no, sku_barcode=goods_no,
        goods_name=f"货品 {goods_no}", quantity=Decimal(qty),
        unit_price_tax=Decimal(price) if price is not None else None,
        matched_sku_id=sku_id, match_status="auto" if sku_id else "",
    )
    db.add(it)
    db.flush()
    return it


def _mk_link(db, po_id: int, doc_id: int) -> int:
    link = ProcurementChainLink(
        external_po_id=po_id, target_type="inbound", target_id=doc_id,
        match_method="manual", confirmed=True,
    )
    db.add(link)
    db.flush()
    return link.id


def test_seed_excludes_item_rows_claimed_by_other_po(db_session):
    """行 A 的明细已反填给 PO1 后，PO2 反填同一入库单必须跳过这些行。"""
    sku1 = _mk_sku(db_session, "EXCL-001")
    sku2 = _mk_sku(db_session, "EXCL-002")
    doc = _mk_doc(db_session, "RK-EXCL-001", [])
    it1 = _mk_item(db_session, doc.id, 1, "G-EXCL-1", "10", "2.5", sku1.id)
    it2 = _mk_item(db_session, doc.id, 2, "G-EXCL-2", "5", "1.0", sku2.id)
    po1 = _mk_po(db_session, "EXCL-PO-1")
    po2 = _mk_po(db_session, "EXCL-PO-2")
    _mk_link(db_session, po1.id, doc.id)
    _mk_link(db_session, po2.id, doc.id)

    try:
        first = seed_allocations_for_po(db_session, po1)
        assert first["seeded"] == 2
        rows1 = db_session.query(PurchaseAllocationItem).filter_by(po_id=po1.id).all()
        assert {r.source_item_id for r in rows1} == {it1.id, it2.id}

        second = seed_allocations_for_po(db_session, po2)
        assert second["seeded"] == 0
        assert second["skippedTaken"] == 2
        assert db_session.query(PurchaseAllocationItem).filter_by(po_id=po2.id).count() == 0
    finally:
        db_session.query(PurchaseAllocationItem).filter_by(po_id=po1.id).delete(synchronize_session=False)
        db_session.query(PurchaseAllocationItem).filter_by(po_id=po2.id).delete(synchronize_session=False)
        db_session.query(ProcurementChainLink).filter_by(target_type="inbound", target_id=doc.id).delete(synchronize_session=False)
        for obj in (po1, po2, it1, it2, doc, sku1, sku2):
            db_session.delete(obj)
        db_session.commit()


def test_seed_allows_second_line_of_same_sku(db_session):
    """同 doc 同 SKU 两行：PO1 占第 1 行后，PO2 仍可反填第 2 行（行级排他）。"""
    sku = _mk_sku(db_session, "EXCL-003")
    doc = _mk_doc(db_session, "RK-EXCL-002", [])
    it1 = _mk_item(db_session, doc.id, 1, "G-EXCL-3", "100", "3.0", sku.id)
    it2 = _mk_item(db_session, doc.id, 2, "G-EXCL-3", "200", "3.0", sku.id)
    po1 = _mk_po(db_session, "EXCL-PO-3")
    po2 = _mk_po(db_session, "EXCL-PO-4")
    _mk_link(db_session, po1.id, doc.id)
    _mk_link(db_session, po2.id, doc.id)

    try:
        assert seed_allocations_for_po(db_session, po1)["seeded"] == 1
        assert db_session.query(PurchaseAllocationItem).filter_by(po_id=po1.id).one().source_item_id == it1.id

        second = seed_allocations_for_po(db_session, po2)
        assert second["seeded"] == 1
        row2 = db_session.query(PurchaseAllocationItem).filter_by(po_id=po2.id).one()
        assert row2.source_item_id == it2.id
        assert row2.quantity == Decimal("200")
    finally:
        db_session.query(PurchaseAllocationItem).filter_by(po_id=po1.id).delete(synchronize_session=False)
        db_session.query(PurchaseAllocationItem).filter_by(po_id=po2.id).delete(synchronize_session=False)
        db_session.query(ProcurementChainLink).filter_by(target_type="inbound", target_id=doc.id).delete(synchronize_session=False)
        for obj in (po1, po2, it1, it2, doc, sku):
            db_session.delete(obj)
        db_session.commit()


def test_seed_legacy_row_falls_back_to_doc_sku_exclusivity(db_session):
    """历史行无 source_item_id：按 (doc, sku) 保守排他——同 doc 的该 SKU 不再反填给第二个 PO。"""
    sku1 = _mk_sku(db_session, "EXCL-004")
    sku2 = _mk_sku(db_session, "EXCL-005")
    doc = _mk_doc(db_session, "RK-EXCL-003", [])
    it1 = _mk_item(db_session, doc.id, 1, "G-EXCL-4", "10", "2.5", sku1.id)
    it2 = _mk_item(db_session, doc.id, 2, "G-EXCL-5", "5", "1.0", sku2.id)
    po1 = _mk_po(db_session, "EXCL-PO-5")
    po2 = _mk_po(db_session, "EXCL-PO-6")
    _mk_link(db_session, po1.id, doc.id)
    _mk_link(db_session, po2.id, doc.id)
    # 旧口径占用行：有 note 无 source_item_id
    legacy = PurchaseAllocationItem(
        po_id=po1.id, sku_id=sku1.id, sku_code=sku1.sku_code, goods_name=sku1.sku_name,
        quantity=Decimal("10"), unit_price=Decimal("2.5"), amount=Decimal("25"),
        source="inbound_auto", note=f"由入库单 #{doc.id} 明细自动反填",
    )
    db_session.add(legacy)
    db_session.flush()

    try:
        result = seed_allocations_for_po(db_session, po2)
        assert result["seeded"] == 1
        rows2 = db_session.query(PurchaseAllocationItem).filter_by(po_id=po2.id).all()
        assert [r.sku_id for r in rows2] == [sku2.id]  # sku1 被 (doc,sku) 占用挡住
    finally:
        db_session.query(PurchaseAllocationItem).filter_by(po_id=po1.id).delete(synchronize_session=False)
        db_session.query(PurchaseAllocationItem).filter_by(po_id=po2.id).delete(synchronize_session=False)
        db_session.query(ProcurementChainLink).filter_by(target_type="inbound", target_id=doc.id).delete(synchronize_session=False)
        for obj in (po1, po2, it1, it2, doc, sku1, sku2):
            db_session.delete(obj)
        db_session.commit()


def test_seed_edited_row_still_claims_item(db_session):
    """人工编辑后的行（source=manual）仍占用来源明细行，其他 PO 不能重复反填。"""
    sku = _mk_sku(db_session, "EXCL-006")
    doc = _mk_doc(db_session, "RK-EXCL-004", [])
    it = _mk_item(db_session, doc.id, 1, "G-EXCL-6", "10", "2.0", sku.id)
    po1 = _mk_po(db_session, "EXCL-PO-7")
    po2 = _mk_po(db_session, "EXCL-PO-8")
    _mk_link(db_session, po1.id, doc.id)
    _mk_link(db_session, po2.id, doc.id)
    edited = PurchaseAllocationItem(
        po_id=po1.id, sku_id=sku.id, sku_code=sku.sku_code, goods_name=sku.sku_name,
        quantity=Decimal("8"), unit_price=Decimal("2.0"), amount=Decimal("16"),
        source="manual", note=f"由入库单 #{doc.id} 明细自动反填", source_item_id=it.id,
    )
    db_session.add(edited)
    db_session.flush()

    try:
        result = seed_allocations_for_po(db_session, po2)
        assert result["seeded"] == 0
        assert result["skippedTaken"] == 1
        assert db_session.query(PurchaseAllocationItem).filter_by(po_id=po2.id).count() == 0
    finally:
        db_session.query(PurchaseAllocationItem).filter_by(po_id=po1.id).delete(synchronize_session=False)
        db_session.query(PurchaseAllocationItem).filter_by(po_id=po2.id).delete(synchronize_session=False)
        db_session.query(ProcurementChainLink).filter_by(target_type="inbound", target_id=doc.id).delete(synchronize_session=False)
        for obj in (po1, po2, it, doc, sku):
            db_session.delete(obj)
        db_session.commit()


def test_seed_same_sku_across_two_docs_creates_two_rows(db_session):
    """拆分收货：同一 PO 的同 SKU 出现在两张关联入库单时，各建一行，数量不丢。"""
    sku = _mk_sku(db_session, "EXCL-007")
    doc_a = _mk_doc(db_session, "RK-EXCL-005", [])
    doc_b = _mk_doc(db_session, "RK-EXCL-006", [])
    it_a = _mk_item(db_session, doc_a.id, 1, "G-EXCL-7", "100", "1.0", sku.id)
    it_b = _mk_item(db_session, doc_b.id, 1, "G-EXCL-7", "100", "1.0", sku.id)
    po = _mk_po(db_session, "EXCL-PO-9")
    _mk_link(db_session, po.id, doc_a.id)
    _mk_link(db_session, po.id, doc_b.id)

    try:
        first = seed_allocations_for_po(db_session, po)
        assert first["seeded"] == 2
        rows = db_session.query(PurchaseAllocationItem).filter_by(po_id=po.id).all()
        assert sorted(r.source_item_id for r in rows) == sorted([it_a.id, it_b.id])
        assert sum(r.quantity for r in rows) == Decimal("200")

        again = seed_allocations_for_po(db_session, po)
        assert again["seeded"] == 0  # 幂等
    finally:
        db_session.query(PurchaseAllocationItem).filter_by(po_id=po.id).delete(synchronize_session=False)
        db_session.query(ProcurementChainLink).filter_by(target_type="inbound", target_id=doc_a.id).delete(synchronize_session=False)
        db_session.query(ProcurementChainLink).filter_by(target_type="inbound", target_id=doc_b.id).delete(synchronize_session=False)
        for obj in (po, it_a, it_b, doc_a, doc_b, sku):
            db_session.delete(obj)
        db_session.commit()


def test_seed_same_doc_same_sku_takes_single_line(db_session):
    """同一张单同 SKU 多行（各属不同订单）：一个 PO 只反填第一行。"""
    sku = _mk_sku(db_session, "EXCL-008")
    doc = _mk_doc(db_session, "RK-EXCL-007", [])
    it1 = _mk_item(db_session, doc.id, 1, "G-EXCL-8", "500", "1.0", sku.id)
    it2 = _mk_item(db_session, doc.id, 2, "G-EXCL-8", "500", "0.9", sku.id)
    po = _mk_po(db_session, "EXCL-PO-10")
    _mk_link(db_session, po.id, doc.id)

    try:
        result = seed_allocations_for_po(db_session, po)
        assert result["seeded"] == 1
        rows = db_session.query(PurchaseAllocationItem).filter_by(po_id=po.id).all()
        assert [r.source_item_id for r in rows] == [it1.id]
        assert rows[0].quantity == Decimal("500")
    finally:
        db_session.query(PurchaseAllocationItem).filter_by(po_id=po.id).delete(synchronize_session=False)
        db_session.query(ProcurementChainLink).filter_by(target_type="inbound", target_id=doc.id).delete(synchronize_session=False)
        for obj in (po, it1, it2, doc, sku):
            db_session.delete(obj)
        db_session.commit()


def test_seed_preserves_source_amount_when_unit_price_is_rounded(db_session):
    """入库文件以总金额为事实时，分配金额不能被四位单价反算尾差覆盖。"""
    sku = _mk_sku(db_session, "AMOUNT-001")
    doc = _mk_doc(db_session, "RK-AMOUNT-001", [])
    item = _mk_item(db_session, doc.id, 1, "G-AMOUNT-1", "197", "9.8477", sku.id)
    item.amount_tax = Decimal("1940")
    po = _mk_po(db_session, "AMOUNT-PO-1")
    _mk_link(db_session, po.id, doc.id)

    try:
        result = seed_allocations_for_po(db_session, po)
        assert result["seeded"] == 1
        row = db_session.query(PurchaseAllocationItem).filter_by(po_id=po.id).one()
        assert row.unit_price == Decimal("9.8477")
        assert row.amount == Decimal("1940.0000")
    finally:
        db_session.query(PurchaseAllocationItem).filter_by(po_id=po.id).delete(synchronize_session=False)
        db_session.query(ProcurementChainLink).filter_by(target_type="inbound", target_id=doc.id).delete(synchronize_session=False)
        for obj in (po, item, doc, sku):
            db_session.delete(obj)
        db_session.commit()


def test_seed_prefers_original_order_marker_for_same_doc_same_sku(db_session):
    """同一入库单已有原始订单标记时，按标记取行而不是按表格顺序取行。"""
    sku = _mk_sku(db_session, "EXCL-010")
    doc = _mk_doc(db_session, "RK-EXCL-009", [])
    it_a = _mk_item(db_session, doc.id, 1, "G-EXCL-10", "500", "1.0", sku.id)
    it_b = _mk_item(db_session, doc.id, 2, "G-EXCL-10", "500", "0.9", sku.id)
    po = _mk_po(db_session, "EXCL-PO-MARK-B", platform="1688")
    it_a.raw = {"_1688采购订单": "EXCL-PO-MARK-A"}
    it_b.raw = {"_1688采购订单": po.external_order_id}
    _mk_link(db_session, po.id, doc.id)

    try:
        result = seed_allocations_for_po(db_session, po)
        assert result["seeded"] == 1
        row = db_session.query(PurchaseAllocationItem).filter_by(po_id=po.id).one()
        assert row.source_item_id == it_b.id
        assert row.unit_price == Decimal("0.9")
    finally:
        db_session.query(PurchaseAllocationItem).filter_by(po_id=po.id).delete(synchronize_session=False)
        db_session.query(ProcurementChainLink).filter_by(target_type="inbound", target_id=doc.id).delete(synchronize_session=False)
        for obj in (po, it_a, it_b, doc, sku):
            db_session.delete(obj)
        db_session.commit()


def test_legacy_inbound_link_resolves_goodsdoc_no(db_session):
    """旧入库关联按实际 goodsdoc_no 解析，不读取不存在的 document_id 字段。"""
    po = _mk_po(db_session, "EXCL-PO-LEGACY-DOC")
    doc = _mk_doc(db_session, "RK-EXCL-LEGACY-DOC", [])
    db_session.add(InboundLink(po_id=po.id, goodsdoc_no=doc.goodsdoc_no))
    db_session.flush()

    try:
        assert collect_linked_doc_ids_for_po(db_session, po) == {doc.id}
    finally:
        db_session.query(InboundLink).filter_by(po_id=po.id).delete(synchronize_session=False)
        db_session.delete(doc)
        db_session.delete(po)
        db_session.commit()


def test_remove_link_releases_seeded_rows(db_session):
    """解除入库单关联：释放反填行（仅可编辑 PO），明细行占用随之消失。"""
    sku = _mk_sku(db_session, "EXCL-009")
    doc = _mk_doc(db_session, "RK-EXCL-008", [])
    it = _mk_item(db_session, doc.id, 1, "G-EXCL-9", "10", "2.0", sku.id)
    po = _mk_po(db_session, "EXCL-PO-11")
    link_id = _mk_link(db_session, po.id, doc.id)
    seed_allocations_for_po(db_session, po)
    assert db_session.query(PurchaseAllocationItem).filter_by(po_id=po.id).count() == 1

    try:
        matcher = ProcurementChainMatcher(db_session)
        matcher.remove_link(link_id)
        assert db_session.query(PurchaseAllocationItem).filter_by(po_id=po.id).count() == 0
        # 释放后其他 PO 可以重新反填该明细行
        po2 = _mk_po(db_session, "EXCL-PO-12")
        _mk_link(db_session, po2.id, doc.id)
        result = seed_allocations_for_po(db_session, po2)
        assert result["seeded"] == 1
    finally:
        db_session.query(PurchaseAllocationItem).delete(synchronize_session=False)
        db_session.query(ProcurementChainLink).filter_by(target_type="inbound", target_id=doc.id).delete(synchronize_session=False)
        for obj in (po, it, doc, sku):
            db_session.delete(obj)
        db_session.commit()


def test_seed_creates_payment_gap_exception_without_auto_approval(db_session):
    """1688 实付与入库金额不一致时进入异常中心，不能自动审批。"""
    sku = _mk_sku(db_session, "COUPON-001")
    doc = _mk_doc(db_session, "RK-COUPON-001", [])
    item = _mk_item(db_session, doc.id, 1, sku.sku_code, "2", "10", sku.id)
    item.raw = {"_1688采购订单": "COUPON-PO-001"}
    po = ExternalPurchaseOrder(
        external_order_id="COUPON-PO-001",
        platform="1688",
        order_amount=Decimal("20"),
        paid_amount=Decimal("19.13"),
        purchase_status="pending_refine",
        raw={
            "_raw": {
                "originalSumPayment": "2000",
                "sumPayment": "1913",
                "promotionFeeMap": {"coupon": "87"},
            }
        },
    )
    db_session.add(po)
    db_session.flush()
    _mk_link(db_session, po.id, doc.id)

    try:
        result = seed_allocations_for_po(db_session, po)
        db_session.refresh(po)
        assert result["seeded"] == 1
        assert result["paymentGap"] == "0.8700"
        assert po.adjustment_amount is None
        assert po.purchase_status == "pending_refine"
        exception = db_session.query(ExceptionRecord).filter_by(
            code="PURCHASE_PAYMENT_GAP", ref_id=str(po.id)
        ).one()
        assert exception.status == "pending"
        assert exception.detail["paidAmount"] == "19.13"
        assert exception.detail["inboundAmount"] == "20.0000"
    finally:
        db_session.query(PurchaseAllocationItem).filter_by(po_id=po.id).delete(synchronize_session=False)
        db_session.query(ProcurementChainLink).filter_by(target_type="inbound", target_id=doc.id).delete(synchronize_session=False)
        for obj in (po, item, doc, sku):
            db_session.delete(obj)
        db_session.commit()
