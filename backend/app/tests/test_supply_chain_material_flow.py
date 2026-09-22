from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.catalog import ProductSku
from app.models.consumable import Consumable, ConsumableSkuMapping
from app.services.production_material_flow_service import consume_materials, dispatch_materials, receive_materials
from app.services.production_service import cancel_production_order, create_production_order, production_order_detail


def _sku(db_session) -> ProductSku:
    row = ProductSku(
        jackyun_sku_id=f"pytest-flow-{uuid4().hex}",
        sku_code=f"FLOW-SKU-{uuid4().hex[:8]}",
        sku_name="耗材流转测试 SKU",
        unit="盒",
        status="active",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _material(db_session, stock: str = "100") -> Consumable:
    row = Consumable(
        code=f"FLOW-HC-{uuid4().hex[:8]}",
        name="耗材流转测试彩盒",
        category="彩盒",
        unit="个",
        stock_qty=Decimal(stock),
        factory_qty=Decimal("0"),
        transit_qty=Decimal("0"),
    )
    db_session.add(row)
    db_session.flush()
    return row


def _order(db_session):
    sku = _sku(db_session)
    material = _material(db_session)
    db_session.add(ConsumableSkuMapping(
        consumable_id=material.id,
        sku_id=sku.id,
        usage_per_unit=Decimal("1"),
        note="pytest flow",
    ))
    db_session.flush()
    order = create_production_order(
        db_session,
        factory_name="测试代工厂",
        items=[{"sku_id": sku.id, "quantity": Decimal("80")}],
        actor="pytest",
    )
    reservation_id = production_order_detail(db_session, order)["materials"][0]["id"]
    return order, material, reservation_id


def test_material_dispatch_receive_and_factory_consume_move_correct_stock(db_session):
    order, material, reservation_id = _order(db_session)

    dispatch_materials(
        db_session,
        order_id=order.id,
        items=[{"reservation_id": reservation_id, "quantity": "30"}],
        actor="pytest",
        request_key=str(uuid4()),
        carrier="测试物流",
        tracking_no="TRACK-001",
    )
    db_session.refresh(material)
    detail = production_order_detail(db_session, order)
    reservation = detail["materials"][0]
    assert material.stock_qty == Decimal("70")
    assert material.transit_qty == Decimal("30")
    assert material.factory_qty == Decimal("0")
    assert reservation["reservedQty"] == "50.0000"
    assert reservation["dispatchedQty"] == "30.0000"
    assert reservation["factoryReceivedQty"] == "0.0000"

    receive_key = str(uuid4())
    receive_materials(
        db_session,
        order_id=order.id,
        items=[{"reservation_id": reservation_id, "quantity": "20"}],
        actor="pytest",
        request_key=receive_key,
    )
    db_session.refresh(material)
    assert material.stock_qty == Decimal("70")
    assert material.transit_qty == Decimal("10")
    assert material.factory_qty == Decimal("20")

    # 同一个签收 request_key 重复提交必须幂等。
    receive_materials(
        db_session,
        order_id=order.id,
        items=[{"reservation_id": reservation_id, "quantity": "20"}],
        actor="pytest",
        request_key=receive_key,
    )
    db_session.refresh(material)
    assert material.transit_qty == Decimal("10")
    assert material.factory_qty == Decimal("20")

    consume_key = str(uuid4())
    consume_materials(
        db_session,
        order_id=order.id,
        items=[{"reservation_id": reservation_id, "quantity": "7"}],
        actor="pytest",
        request_key=consume_key,
        note="实际生产消耗",
    )
    db_session.refresh(material)
    detail = production_order_detail(db_session, order)
    reservation = detail["materials"][0]
    # 工厂实际消耗只能扣工厂库存，不能再误扣自有仓。
    assert material.stock_qty == Decimal("70")
    assert material.transit_qty == Decimal("10")
    assert material.factory_qty == Decimal("13")
    assert material.used_qty == Decimal("7")
    assert reservation["factoryReceivedQty"] == "20.0000"
    assert reservation["consumedQty"] == "7.0000"

    # 同一个消耗 request_key 重放不重复扣库存。
    consume_materials(
        db_session,
        order_id=order.id,
        items=[{"reservation_id": reservation_id, "quantity": "7"}],
        actor="pytest",
        request_key=consume_key,
        note="实际生产消耗",
    )
    db_session.refresh(material)
    assert material.stock_qty == Decimal("70")
    assert material.factory_qty == Decimal("13")
    assert material.used_qty == Decimal("7")

    with pytest.raises(ValueError, match="超过该生产单工厂可用耗材"):
        consume_materials(
            db_session,
            order_id=order.id,
            items=[{"reservation_id": reservation_id, "quantity": "14"}],
            actor="pytest",
            request_key=str(uuid4()),
        )


def test_material_flow_rejects_over_dispatch_over_receive_and_cancel_after_dispatch(db_session):
    order, material, reservation_id = _order(db_session)

    # 纯业务校验异常不会破坏数据库事务；这里不能 rollback，否则会把测试准备数据一起撤销。
    with pytest.raises(ValueError, match="超过当前预占"):
        dispatch_materials(
            db_session,
            order_id=order.id,
            items=[{"reservation_id": reservation_id, "quantity": "81"}],
            actor="pytest",
            request_key=str(uuid4()),
        )

    dispatch_materials(
        db_session,
        order_id=order.id,
        items=[{"reservation_id": reservation_id, "quantity": "30"}],
        actor="pytest",
        request_key=str(uuid4()),
    )

    with pytest.raises(ValueError, match="超过该生产单在途"):
        receive_materials(
            db_session,
            order_id=order.id,
            items=[{"reservation_id": reservation_id, "quantity": "31"}],
            actor="pytest",
            request_key=str(uuid4()),
        )

    with pytest.raises(ValueError, match="已有耗材发往工厂"):
        cancel_production_order(db_session, order.id)

    db_session.refresh(material)
    assert material.stock_qty == Decimal("70")
    assert material.transit_qty == Decimal("30")
