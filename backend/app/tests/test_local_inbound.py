from datetime import date, datetime
from decimal import Decimal
import re

import pytest
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.models.catalog import ProductSku, Warehouse
from app.models.consumable import Consumable
from app.models.consumable_purchase import ConsumablePurchaseItem
from app.models.jackyun import JackyunGoodsDocumentItem
from app.models.purchase import ExternalPurchaseOrder, PurchaseAllocationItem
from app.config import settings
from app.services import consumable_purchase_service, purchase_service
from app.services.local_inbound_service import _number, create_purchase_inbound, delete_local_purchase_inbound


def test_local_purchase_inbound_uses_system_number_and_links_allocation(db_session):
    po = ExternalPurchaseOrder(
        external_order_id="1688-LOCAL-001",
        platform="1688",
        supplier_name="本地供应商",
        purchase_status="confirmed",
    )
    sku = ProductSku(
        jackyun_sku_id=f"J-{uuid4().hex}", sku_code="LOCAL-SKU-001", sku_name="本系统货品", unit="件",
    )
    warehouse = Warehouse(code=f"LOCAL-{uuid4().hex[:8]}", name="本地正品仓", purpose="goods", status="active")
    db_session.add_all([po, sku, warehouse])
    db_session.flush()
    allocation = PurchaseAllocationItem(
        po_id=po.id, sku_id=sku.id, sku_code=sku.sku_code, goods_name=sku.sku_name,
        quantity=Decimal("10"), unit_price=Decimal("12.50"), amount=Decimal("125"), source="manual",
    )
    db_session.add(allocation)
    db_session.flush()

    document, link = create_purchase_inbound(
        db_session,
        order_id=-po.id,
        warehouse_id=warehouse.id,
        items=[{"allocation_id": allocation.id, "quantity": Decimal("10")}],
        actor="pytest",
    )

    expected_date = datetime.now(ZoneInfo(settings.TZ)).strftime("%Y%m%d")
    assert re.fullmatch(rf"RK{expected_date}\d{{4}}", document.goodsdoc_no)
    next_number = _number(db_session, "", datetime.now(ZoneInfo(settings.TZ)))
    assert next_number.endswith(f"{int(document.goodsdoc_no[-4:]) + 1:04d}")
    assert _number(db_session, "RK-MANUAL-001", None) == "RK-MANUAL-001"
    assert document.raw["source"] == "local_purchase_inbound"
    assert document.raw["platformPurchaseOrderNo"] == po.external_order_id
    assert link.external_po_id == po.id
    assert link.match_method == "local"
    item = db_session.query(JackyunGoodsDocumentItem).filter_by(document_id=document.id).one()
    assert item.matched_sku_id == sku.id
    assert allocation.note == f"由入库单 #{document.id} 明细自动反填"

    db_session.delete(link)
    db_session.delete(item)
    db_session.delete(document)
    db_session.delete(allocation)
    db_session.delete(po)
    db_session.delete(sku)
    db_session.delete(warehouse)
    db_session.commit()


def test_consumable_purchase_number_uses_platform_order_reference(db_session):
    material = Consumable(
        code=f"HC-{uuid4().hex[:10]}", name="本地耗材", unit="个", status="active",
        stock_qty=Decimal("0"), factory_qty=Decimal("0"), transit_qty=Decimal("0"),
        purchased_qty=Decimal("0"), used_qty=Decimal("0"), min_stock_qty=Decimal("0"),
    )
    db_session.add(material)
    db_session.flush()

    purchase = consumable_purchase_service.create_purchase(
        db_session,
        request_key=str(uuid4()),
        supplier_name="耗材供应商",
        ordered_on=date(2026, 9, 15),
        reference_no="1688-PLATFORM-002",
        items=[{"consumable_id": material.id, "quantity": Decimal("5"), "unit_cost": Decimal("2.5")}],
        actor="pytest",
    )

    assert purchase.number == "HC1688-PLATFORM-002"

    db_session.query(ConsumablePurchaseItem).filter_by(purchase_id=purchase.id).delete(synchronize_session=False)
    db_session.delete(purchase)
    db_session.delete(material)
    db_session.commit()


def test_purchase_allocation_uses_total_to_calculate_unit_price(db_session):
    po = ExternalPurchaseOrder(
        external_order_id=f"1688-TOTAL-{uuid4().hex[:8]}",
        platform="1688",
        supplier_name="总价测试供应商",
        purchase_status="pending_refine",
    )
    sku = ProductSku(
        jackyun_sku_id=f"J-{uuid4().hex}", sku_code=f"TOTAL-SKU-{uuid4().hex[:8]}", sku_name="总价测试货品", unit="件",
    )
    db_session.add_all([po, sku])
    db_session.flush()

    allocation = purchase_service.add_allocation(
        db_session,
        po,
        sku_id=sku.id,
        sku_code=sku.sku_code,
        goods_name=sku.sku_name,
        quantity="3",
        amount="100",
        actor="pytest",
    )

    assert allocation.amount == Decimal("100.0000")
    assert allocation.unit_price == Decimal("33.3333")

    db_session.delete(allocation)
    db_session.delete(po)
    db_session.delete(sku)
    db_session.commit()

def test_local_purchase_inbound_allows_cumulative_partial_receipts(db_session):
    po = ExternalPurchaseOrder(
        external_order_id=f"1688-PARTIAL-{uuid4().hex[:8]}",
        platform="1688", supplier_name="分批到货供应商", purchase_status="confirmed",
    )
    sku = ProductSku(
        jackyun_sku_id=f"J-{uuid4().hex}", sku_code=f"PARTIAL-SKU-{uuid4().hex[:8]}",
        sku_name="分批到货货品", unit="件",
    )
    warehouse = Warehouse(
        code=f"PARTIAL-{uuid4().hex[:8]}", name="分批到货仓", purpose="goods", status="active"
    )
    db_session.add_all([po, sku, warehouse])
    db_session.flush()
    allocation = PurchaseAllocationItem(
        po_id=po.id, sku_id=sku.id, sku_code=sku.sku_code, goods_name=sku.sku_name,
        quantity=Decimal("10"), unit_price=Decimal("2"), amount=Decimal("20"), source="manual",
    )
    db_session.add(allocation)
    db_session.commit()

    first, _ = create_purchase_inbound(
        db_session, order_id=-po.id, warehouse_id=warehouse.id,
        items=[{"allocation_id": allocation.id, "quantity": Decimal("5")}], actor="pytest",
    )
    db_session.refresh(allocation)
    assert "累计入库 5" in allocation.note

    second, _ = create_purchase_inbound(
        db_session, order_id=-po.id, warehouse_id=warehouse.id,
        items=[{"allocation_id": allocation.id, "quantity": Decimal("5")}], actor="pytest",
    )
    db_session.refresh(allocation)
    assert allocation.note == f"由入库单 #{second.id} 明细自动反填"

    with pytest.raises(ValueError, match="已全部入库"):
        create_purchase_inbound(
            db_session, order_id=-po.id, warehouse_id=warehouse.id,
            items=[{"allocation_id": allocation.id, "quantity": Decimal("1")}], actor="pytest",
        )

    delete_local_purchase_inbound(db_session, document_id=second.id)
    db_session.refresh(allocation)
    assert "累计入库 5" in allocation.note
    delete_local_purchase_inbound(db_session, document_id=first.id)
    db_session.refresh(allocation)
    assert allocation.source == "manual"

    db_session.delete(allocation)
    db_session.delete(po)
    db_session.delete(sku)
    db_session.delete(warehouse)
    db_session.commit()
