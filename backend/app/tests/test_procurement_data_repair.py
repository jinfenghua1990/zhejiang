"""历史采购明细来源指针修复测试。"""

from decimal import Decimal

from app.models.catalog import ProductSku
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.models.org import AuditLog
from app.models.purchase import ExternalPurchaseOrder, PurchaseAllocationItem
from app.services.procurement_data_repair import (
    repair_unambiguous_inbound_allocation_sources,
)


def test_repair_uses_unique_order_marker_and_releases_wrong_pointer(db_session):
    sku = ProductSku(
        jackyun_sku_id="REPAIR-SKU-001",
        sku_code="REPAIR-SKU-001",
        sku_name="修复测试货品",
        cost_mode="dynamic",
    )
    doc = JackyunGoodsDocument(
        document_type="inbound",
        goodsdoc_no="RK-REPAIR-001",
        warehouse_code="WH",
        warehouse_name="测试仓",
    )
    po_a = ExternalPurchaseOrder(
        external_order_id="REPAIR-PO-A",
        platform="1688",
        purchase_status="pending_refine",
        raw={},
    )
    po_b = ExternalPurchaseOrder(
        external_order_id="REPAIR-PO-B",
        platform="1688",
        purchase_status="pending_refine",
        raw={},
    )
    db_session.add_all([sku, doc, po_a, po_b])
    db_session.flush()
    item_a = JackyunGoodsDocumentItem(
        document_id=doc.id,
        line_no=1,
        goods_no=sku.sku_code,
        sku_barcode=sku.sku_code,
        goods_name=sku.sku_name,
        quantity=Decimal("10"),
        unit_price_tax=Decimal("2"),
        matched_sku_id=sku.id,
        raw={"_1688采购订单": po_a.external_order_id},
    )
    item_b = JackyunGoodsDocumentItem(
        document_id=doc.id,
        line_no=2,
        goods_no=sku.sku_code,
        sku_barcode=sku.sku_code,
        goods_name=sku.sku_name,
        quantity=Decimal("10"),
        unit_price_tax=Decimal("3"),
        matched_sku_id=sku.id,
        raw={"_1688采购订单": po_b.external_order_id},
    )
    db_session.add_all([item_a, item_b])
    db_session.flush()
    row_a = PurchaseAllocationItem(
        po_id=po_a.id,
        sku_id=sku.id,
        sku_code=sku.sku_code,
        goods_name=sku.sku_name,
        quantity=Decimal("10"),
        unit_price=Decimal("2"),
        amount=Decimal("20"),
        source="manual",
        note=f"由入库单 #{doc.id} 明细自动反填",
    )
    # 旧逻辑把第二个订单错误地占用了第一行；修复应允许两行交换到各自的唯一标记。
    row_b = PurchaseAllocationItem(
        po_id=po_b.id,
        sku_id=sku.id,
        sku_code=sku.sku_code,
        goods_name=sku.sku_name,
        quantity=Decimal("10"),
        unit_price=Decimal("3"),
        amount=Decimal("30"),
        source="inbound_auto",
        source_item_id=item_a.id,
        note=f"由入库单 #{doc.id} 明细自动反填",
    )
    db_session.add_all([row_a, row_b])
    db_session.flush()

    try:
        result = repair_unambiguous_inbound_allocation_sources(
            db_session, actor="test-source-repair"
        )
        assert result["repaired"] == 2
        assert row_a.source_item_id == item_a.id
        assert row_b.source_item_id == item_b.id
    finally:
        db_session.query(PurchaseAllocationItem).filter(
            PurchaseAllocationItem.id.in_([row_a.id, row_b.id])
        ).delete(synchronize_session=False)
        db_session.query(AuditLog).filter_by(actor="test-source-repair").delete(
            synchronize_session=False
        )
        for obj in (item_a, item_b, doc, po_a, po_b, sku):
            db_session.delete(obj)
        db_session.commit()
