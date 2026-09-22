from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from app.api.v1.supply_chain import replenishment
from app.models.catalog import ProductSku
from app.models.consumable import Consumable, ConsumableSkuMapping
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.models.production import ProductionOrderItem
from app.models.sales import SalesOrder, SalesOrderItem
from app.services.production_service import (
    cancel_production_order,
    create_production_order,
    production_order_detail,
    recalculate_production_materials,
)
from app.services.inventory_position_service import current_positions


def _sku(db_session, suffix: str) -> ProductSku:
    row = ProductSku(
        jackyun_sku_id=f"pytest-jky-{suffix}-{uuid4().hex}",
        sku_code=f"PYTEST-SKU-{suffix}-{uuid4().hex[:8]}",
        sku_name=f"测试生产 SKU {suffix}",
        unit="盒",
        status="active",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _consumable(db_session, suffix: str, stock: str) -> Consumable:
    row = Consumable(
        code=f"PYTEST-HC-{suffix}-{uuid4().hex[:8]}",
        name=f"测试耗材 {suffix}",
        category="彩盒",
        unit="个",
        stock_qty=Decimal(stock),
        factory_qty=Decimal("0"),
        transit_qty=Decimal("0"),
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_production_material_reservation_does_not_double_allocate(db_session):
    sku = _sku(db_session, "A")
    material = _consumable(db_session, "A", "100")
    db_session.add(
        ConsumableSkuMapping(
            consumable_id=material.id,
            sku_id=sku.id,
            usage_per_unit=Decimal("2"),
            note="pytest",
        )
    )
    db_session.flush()

    first = create_production_order(
        db_session,
        factory_name="测试工厂一",
        items=[{"sku_id": sku.id, "quantity": Decimal("40")}],
        actor="pytest",
    )
    first_detail = production_order_detail(db_session, first)
    assert first_detail["materials"][0]["requiredQty"] == "80.0000"
    assert first_detail["materials"][0]["reservedQty"] == "80.0000"
    assert first_detail["materials"][0]["shortageQty"] == "0.0000"

    # 第二张单需要 40 个耗材，但第一张已经预占 80；自有仓 100，所以只能再预占 20。
    second = create_production_order(
        db_session,
        factory_name="测试工厂二",
        items=[{"sku_id": sku.id, "quantity": Decimal("20")}],
        actor="pytest",
    )
    second_detail = production_order_detail(db_session, second)
    assert second_detail["materials"][0]["requiredQty"] == "40.0000"
    assert second_detail["materials"][0]["reservedQty"] == "20.0000"
    assert second_detail["materials"][0]["shortageQty"] == "20.0000"
    assert second_detail["materials"][0]["state"] == "shortage"

    # “预占”不能直接扣实际自有仓库存。
    db_session.refresh(material)
    assert material.stock_qty == Decimal("100")

    # 取消第一张未发料生产单后，第二张重算应拿到完整 40 的预占。
    cancel_production_order(db_session, first.id)
    second = recalculate_production_materials(db_session, second.id)
    second_detail = production_order_detail(db_session, second)
    assert second_detail["materials"][0]["reservedQty"] == "40.0000"
    assert second_detail["materials"][0]["shortageQty"] == "0.0000"
    assert second_detail["materials"][0]["state"] == "reserved"

    db_session.refresh(material)
    assert material.stock_qty == Decimal("100")


def test_replenishment_counts_produced_goods_until_real_inbound(db_session):
    sku = _sku(db_session, "OPEN-SUPPLY")
    now = datetime.now(timezone.utc)
    # 独立运算口径：用一张真实采购入库单建立库存基线 10。
    inbound_doc = JackyunGoodsDocument(document_type="inbound", goodsdoc_no=f"IN-{uuid4().hex}", document_at=now - timedelta(days=40))
    db_session.add(inbound_doc)
    db_session.flush()
    db_session.add(JackyunGoodsDocumentItem(document_id=inbound_doc.id, line_no=1, goods_no=sku.sku_code, quantity=Decimal("10")))
    sale = SalesOrder(
        order_no=f"PYTEST-SALE-{uuid4().hex}",
        source_provider="pytest",
        source_order_id=uuid4().hex,
        platform="pytest",
        order_status="已完成",
        ordered_at=now,
    )
    db_session.add(sale)
    db_session.flush()
    db_session.add(SalesOrderItem(
        order_id=sale.id,
        sku_id=sku.id,
        sku_code=sku.sku_code,
        goods_name=sku.sku_name,
        quantity=Decimal("30"),
    ))
    order = create_production_order(
        db_session,
        factory_name="补货待供应测试工厂",
        items=[{"sku_id": sku.id, "quantity": Decimal("20")}],
        actor="pytest",
    )
    item = db_session.query(ProductionOrderItem).filter_by(production_order_id=order.id).one()
    item.completed_qty = Decimal("20")
    order.status = "produced"
    db_session.commit()

    result = replenishment(days=30, lead_days=14, safety_days=7, search=sku.sku_code, limit=10, db=db_session)
    row = next(value for value in result["rows"] if value["skuId"] == sku.id)
    # 日均 1，目标 21；库存 10；生产完成但尚未入库 20 仍属于待供应，因此建议补 0。
    assert Decimal(row["productionOpenSupplyQuantity"]) == Decimal("20")
    assert Decimal(row["suggestedReplenishment"]) == Decimal("0")

    # 部分关联真实入库 8 后，只剩 12 作为生产待供应。
    # 测试数据必须满足数据库约束：入库数量不能超过已到货数量，已到货不能超过已发货数量。
    item.shipped_qty = Decimal("20")
    item.arrived_qty = Decimal("20")
    item.inbound_qty = Decimal("8")
    order.status = "inbound"
    db_session.commit()
    result = replenishment(days=30, lead_days=14, safety_days=7, search=sku.sku_code, limit=10, db=db_session)
    row = next(value for value in result["rows"] if value["skuId"] == sku.id)
    assert Decimal(row["productionOpenSupplyQuantity"]) == Decimal("12")
    assert Decimal(row["suggestedReplenishment"]) == Decimal("0")


def test_replenishment_prefers_outbound_docs_and_document_based_inventory(db_session):
    """库存独立运算：采购入库 − 销售出库；近销优先使用真实出库单。"""
    sku = _sku(db_session, "OUTBOUND")
    now = datetime.now(timezone.utc)
    inbound = JackyunGoodsDocument(document_type="inbound", goodsdoc_no=f"IN-{uuid4().hex}", document_at=now - timedelta(days=40))
    old_outbound = JackyunGoodsDocument(document_type="outbound", goodsdoc_no=f"OLD-{uuid4().hex}", document_at=now - timedelta(days=40))
    current_outbound = JackyunGoodsDocument(document_type="outbound", goodsdoc_no=f"NEW-{uuid4().hex}", document_at=now)
    db_session.add_all([inbound, old_outbound, current_outbound])
    db_session.flush()
    db_session.add_all([
        JackyunGoodsDocumentItem(document_id=inbound.id, line_no=1, goods_no=sku.sku_code, quantity=Decimal("100")),
        JackyunGoodsDocumentItem(document_id=old_outbound.id, line_no=1, goods_no=sku.sku_code, quantity=Decimal("5")),
        JackyunGoodsDocumentItem(document_id=current_outbound.id, line_no=1, goods_no=sku.sku_code, quantity=Decimal("7")),
    ])
    sale = SalesOrder(
        order_no=f"PYTEST-SALE-OUTBOUND-{uuid4().hex}",
        source_provider="pytest",
        source_order_id=uuid4().hex,
        platform="pytest",
        order_status="已完成",
        ordered_at=now,
    )
    db_session.add(sale)
    db_session.flush()
    db_session.add(SalesOrderItem(
        order_id=sale.id,
        sku_id=sku.id,
        sku_code=sku.sku_code,
        goods_name=sku.sku_name,
        quantity=Decimal("99"),
    ))
    db_session.commit()

    positions = current_positions(db_session)
    assert positions["by_sku"][sku.id] == Decimal("88")

    result = replenishment(days=30, lead_days=14, safety_days=7, search=sku.sku_code, limit=10, db=db_session)
    row = next(value for value in result["rows"] if value["skuId"] == sku.id)
    assert Decimal(row["soldQuantity"]) == Decimal("7")
    assert row["salesSource"] == "sales_outbound"
    assert result["data"]["outboundDocumentCount"] == 1
