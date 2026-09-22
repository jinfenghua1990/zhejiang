from decimal import Decimal
from uuid import uuid4

from app.models.catalog import ProductSku
from app.models.consumable import Consumable, ConsumableSkuMapping, ConsumableTransaction, InboundConsumableUsage
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.models.procurement_chain import ProcurementChainLink
from app.models.purchase import ExternalPurchaseOrder, PurchaseAllocationItem
from app.services.consumable_service import (
    auto_apply_inbound_usage,
    set_inbound_usage,
    suggest_inbound_usage,
    upsert_mapping,
)


def _sku(db_session, suffix: str) -> ProductSku:
    row = ProductSku(
        jackyun_sku_id=f"pytest-inbound-sku-{suffix}-{uuid4().hex}",
        sku_code=f"PYTEST-INBOUND-SKU-{suffix}-{uuid4().hex[:8]}",
        sku_name=f"入库自动耗材测试 SKU {suffix}",
        unit="个",
        status="active",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _material(db_session, suffix: str, stock: str = "1000") -> Consumable:
    row = Consumable(
        code=f"PYTEST-INBOUND-HC-{suffix}-{uuid4().hex[:8]}",
        name=f"入库自动耗材测试 {suffix}",
        category="包装",
        unit="个",
        stock_qty=Decimal(stock),
    )
    db_session.add(row)
    db_session.flush()
    return row


def _chain(db_session, suffix: str, sku: ProductSku, quantity: str):
    po = ExternalPurchaseOrder(
        external_order_id=f"PYTEST-INBOUND-PO-{suffix}-{uuid4().hex}",
        supplier_name="入库自动耗材测试供应商",
        purchase_status="confirmed",
        paid_amount=Decimal("10000"),
    )
    document = JackyunGoodsDocument(
        document_type="inbound",
        goodsdoc_no=f"PYTEST-INBOUND-DOC-{suffix}-{uuid4().hex}",
    )
    db_session.add_all([po, document])
    db_session.flush()
    item = JackyunGoodsDocumentItem(
        document_id=document.id,
        line_no=1,
        goods_no=sku.sku_code,
        goods_name=sku.sku_name,
        quantity=Decimal(quantity),
        matched_sku_id=sku.id,
        match_status="auto",
    )
    link = ProcurementChainLink(
        external_po_id=po.id,
        target_type="inbound",
        target_id=document.id,
        match_method="file_import",
        confirmed=True,
        consumable_usage_decided=True,
        consumable_usage_enabled=False,
        note="自动确认；本次不使用耗材",
    )
    db_session.add_all([item, link])
    db_session.flush()
    allocation = PurchaseAllocationItem(
        po_id=po.id,
        sku_id=sku.id,
        sku_code=sku.sku_code,
        goods_name=sku.sku_name,
        quantity=Decimal(quantity),
        unit_price=Decimal("1"),
        amount=Decimal(quantity),
        source="inbound_auto",
        source_item_id=item.id,
        note=f"由入库单 #{document.id} 明细自动反填",
    )
    db_session.add(allocation)
    db_session.flush()
    return po, document, item, link, allocation


def test_mapping_save_backfills_existing_confirmed_inbound(db_session):
    sku = _sku(db_session, "BACKFILL")
    material = _material(db_session, "BACKFILL")
    _, _, _, link, _ = _chain(db_session, "BACKFILL", sku, "7")

    upsert_mapping(
        db_session,
        mapping_id=None,
        sku_id=sku.id,
        consumable_id=material.id,
        usage_per_unit="2",
        note="pytest",
    )

    db_session.refresh(link)
    db_session.refresh(material)
    usage = db_session.query(InboundConsumableUsage).filter_by(link_id=link.id).one()
    assert usage.quantity == Decimal("14.0000")
    assert link.consumable_usage_enabled is True
    assert material.stock_qty == Decimal("986.0000")
    assert material.used_qty == Decimal("14.0000")


def test_shared_inbound_document_uses_each_purchase_allocation_once(db_session):
    sku_a = _sku(db_session, "SHARED-A")
    sku_b = _sku(db_session, "SHARED-B")
    material_a = _material(db_session, "SHARED-A")
    material_b = _material(db_session, "SHARED-B")
    db_session.add_all([
        ConsumableSkuMapping(consumable_id=material_a.id, sku_id=sku_a.id, usage_per_unit=Decimal("2")),
        ConsumableSkuMapping(consumable_id=material_b.id, sku_id=sku_b.id, usage_per_unit=Decimal("3")),
    ])
    db_session.flush()

    document = JackyunGoodsDocument(
        document_type="inbound",
        goodsdoc_no=f"PYTEST-SHARED-DOC-{uuid4().hex}",
    )
    po_a = ExternalPurchaseOrder(
        external_order_id=f"PYTEST-SHARED-PO-A-{uuid4().hex}",
        purchase_status="confirmed",
        paid_amount=Decimal("10000"),
    )
    po_b = ExternalPurchaseOrder(
        external_order_id=f"PYTEST-SHARED-PO-B-{uuid4().hex}",
        purchase_status="confirmed",
        paid_amount=Decimal("10000"),
    )
    db_session.add_all([document, po_a, po_b])
    db_session.flush()
    item_a = JackyunGoodsDocumentItem(
        document_id=document.id, line_no=1, goods_no=sku_a.sku_code,
        goods_name=sku_a.sku_name, quantity=Decimal("5"), matched_sku_id=sku_a.id,
    )
    item_b = JackyunGoodsDocumentItem(
        document_id=document.id, line_no=2, goods_no=sku_b.sku_code,
        goods_name=sku_b.sku_name, quantity=Decimal("4"), matched_sku_id=sku_b.id,
    )
    link_a = ProcurementChainLink(
        external_po_id=po_a.id, target_type="inbound", target_id=document.id,
        match_method="file_import", confirmed=True,
        consumable_usage_decided=True, consumable_usage_enabled=False,
        note="自动确认；本次不使用耗材",
    )
    link_b = ProcurementChainLink(
        external_po_id=po_b.id, target_type="inbound", target_id=document.id,
        match_method="file_import", confirmed=True,
        consumable_usage_decided=True, consumable_usage_enabled=False,
        note="自动确认；本次不使用耗材",
    )
    db_session.add_all([item_a, item_b, link_a, link_b])
    db_session.flush()
    db_session.add_all([
        PurchaseAllocationItem(
            po_id=po_a.id, sku_id=sku_a.id, sku_code=sku_a.sku_code,
            goods_name=sku_a.sku_name, quantity=Decimal("5"), amount=Decimal("5"),
            source="inbound_auto", source_item_id=item_a.id,
            note=f"由入库单 #{document.id} 明细自动反填",
        ),
        PurchaseAllocationItem(
            po_id=po_b.id, sku_id=sku_b.id, sku_code=sku_b.sku_code,
            goods_name=sku_b.sku_name, quantity=Decimal("4"), amount=Decimal("4"),
            source="inbound_auto", source_item_id=item_b.id,
            note=f"由入库单 #{document.id} 明细自动反填",
        ),
    ])
    db_session.flush()

    assert suggest_inbound_usage(db_session, link_a.id)["items"] == [
        {"consumable_id": material_a.id, "quantity": "10.0000"}
    ]
    assert suggest_inbound_usage(db_session, link_b.id)["items"] == [
        {"consumable_id": material_b.id, "quantity": "12.0000"}
    ]

    assert auto_apply_inbound_usage(db_session, link_a.id)["status"] == "applied"
    assert auto_apply_inbound_usage(db_session, link_b.id)["status"] == "applied"
    assert db_session.query(InboundConsumableUsage).filter_by(link_id=link_a.id).one().quantity == Decimal("10.0000")
    assert db_session.query(InboundConsumableUsage).filter_by(link_id=link_b.id).one().quantity == Decimal("12.0000")
    db_session.refresh(material_a)
    db_session.refresh(material_b)
    assert material_a.used_qty == Decimal("10.0000")
    assert material_b.used_qty == Decimal("12.0000")


def test_explicit_manual_usage_is_not_replaced_by_mapping_backfill(db_session):
    sku = _sku(db_session, "MANUAL")
    material = _material(db_session, "MANUAL", stock="100")
    _, _, _, link, _ = _chain(db_session, "MANUAL", sku, "8")
    link.match_method = "manual"
    link.note = "采购工作台人工选择入库单"
    db_session.flush()

    from app.services.consumable_service import set_inbound_usage

    set_inbound_usage(
        db_session,
        link_id=link.id,
        enabled=True,
        items=[{"consumable_id": material.id, "quantity": "3"}],
        note="人工调整",
    )
    upsert_mapping(
        db_session,
        mapping_id=None,
        sku_id=sku.id,
        consumable_id=material.id,
        usage_per_unit="2",
    )

    usage = db_session.query(InboundConsumableUsage).filter_by(link_id=link.id).one()
    db_session.refresh(material)
    assert usage.quantity == Decimal("3.0000")
    assert material.used_qty == Decimal("3.0000")


def test_recomputing_inbound_usage_keeps_append_only_inventory_ledger(db_session):
    sku = _sku(db_session, "RECALC")
    material = _material(db_session, "RECALC", stock="100")
    _, _, _, link, _ = _chain(db_session, "RECALC", sku, "10")
    link.match_method = "manual"
    db_session.flush()

    set_inbound_usage(
        db_session,
        link_id=link.id,
        enabled=True,
        items=[{"consumable_id": material.id, "quantity": "3"}],
        note="首次耗材登记",
    )
    first_tx = db_session.query(ConsumableTransaction).filter_by(
        source_type="inbound_usage",
        consumable_id=material.id,
        transaction_type="consume",
    ).one()
    first_tx_id = first_tx.id

    set_inbound_usage(
        db_session,
        link_id=link.id,
        enabled=True,
        items=[{"consumable_id": material.id, "quantity": "4"}],
        note="重新核算耗材",
    )

    db_session.refresh(material)
    assert material.stock_qty == Decimal("96.0000")
    assert material.used_qty == Decimal("4.0000")
    assert db_session.get(ConsumableTransaction, first_tx_id) is not None

    consume_rows = db_session.query(ConsumableTransaction).filter_by(
        consumable_id=material.id,
        transaction_type="consume",
    ).order_by(ConsumableTransaction.id).all()
    reversal_rows = db_session.query(ConsumableTransaction).filter_by(
        consumable_id=material.id,
        source_type="inbound_usage_reversal",
    ).all()

    assert [row.quantity for row in consume_rows] == [
        Decimal("3.0000"),
        Decimal("4.0000"),
    ]
    assert len(reversal_rows) == 1
    assert reversal_rows[0].quantity == Decimal("3.0000")
