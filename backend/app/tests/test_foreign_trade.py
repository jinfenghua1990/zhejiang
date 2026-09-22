from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.catalog import ProductSku
from app.models.foreign_trade import (
    ForeignTradeChannel,
    ForeignTradeDealer,
    ForeignTradeInventoryReservation,
    ForeignTradeOrder,
    ForeignTradeSkuMapping,
)
from app.services import foreign_trade_service


def test_foreign_trade_profit_and_overview(db_session):
    db_session.add(ForeignTradeChannel(
        code="pytest-shopify-de",
        name="Pytest Shopify DE",
        channel_type="shopify",
        brand="Alsvid",
        currency="EUR",
        countries=["DE"],
        connected=True,
    ))
    db_session.add(ForeignTradeSkuMapping(
        channel_code="pytest-shopify-de",
        external_sku="ALS-20-CF",
        internal_sku="BIKE-20-CF",
        product_name="Alsvid 20 Carbon",
        status="matched",
    ))
    order = ForeignTradeOrder(
        channel_code="pytest-shopify-de",
        external_order_no="FT-PYTEST-001",
        brand="Alsvid",
        country="DE",
        currency="EUR",
        paid_amount=Decimal("1000"),
        refund_amount=Decimal("50"),
        payment_fee=Decimal("30"),
        purchase_cost=Decimal("500"),
        logistics_cost=Decimal("120"),
        exchange_rate_to_cny=Decimal("8"),
        procurement_status="done",
        fulfillment_status="shipped",
        payment_status="paid",
        status="completed",
    )
    db_session.add(order)
    db_session.flush()

    row = foreign_trade_service.order_dict(order)
    assert Decimal(row["profit"]) == Decimal("300")
    assert Decimal(row["profitCny"]) == Decimal("2400")

    summary = foreign_trade_service.overview(db_session)
    assert summary["orders"] >= 1
    assert summary["channels"] >= 1
    assert summary["skuMappings"] >= 1


def test_foreign_trade_order_api_crud(client):
    payload = {
        "channel_code": "pytest-manual",
        "external_order_no": "FT-API-001",
        "brand": "Alsvid",
        "country": "AT",
        "currency": "EUR",
        "paid_amount": "1200",
        "payment_fee": "36",
        "purchase_cost": "600",
        "logistics_cost": "140",
        "exchange_rate_to_cny": "8",
        "status": "pending",
        "procurement_status": "pending",
        "fulfillment_status": "pending",
        "payment_status": "paid",
    }
    created = client.post("/api/v1/foreign-trade/orders", json=payload)
    assert created.status_code == 201
    order = created.json()
    assert order["externalOrderNo"] == "FT-API-001"
    assert Decimal(order["profit"]) == Decimal("424")
    order_id = order["id"]

    listed = client.get("/api/v1/foreign-trade/orders", params={"q": "FT-API-001"})
    assert listed.status_code == 200
    assert any(row["id"] == order_id for row in listed.json()["items"])

    payload.update({
        "status": "shipped",
        "procurement_status": "done",
        "fulfillment_status": "shipped",
        "tracking_no": "TEST-TRACK-001",
    })
    updated = client.put(f"/api/v1/foreign-trade/orders/{order_id}", json=payload)
    assert updated.status_code == 200
    assert updated.json()["fulfillmentStatus"] == "shipped"

    deleted = client.delete(f"/api/v1/foreign-trade/orders/{order_id}")
    assert deleted.status_code == 200



def test_b2b_dealer_reserved_inventory_is_private(db_session, monkeypatch):
    sku = ProductSku(
        jackyun_sku_id="FT-PRIVATE-SKU-JKY",
        sku_code="FT-PRIVATE-SKU",
        sku_name="Alsvid Test Bike",
        status="active",
    )
    dealer_a = ForeignTradeDealer(code="FT-DEALER-A", company_name="Dealer A GmbH", country="DE")
    dealer_b = ForeignTradeDealer(code="FT-DEALER-B", company_name="Dealer B GmbH", country="DE")
    dealer_c = ForeignTradeDealer(code="FT-DEALER-C", company_name="Dealer C GmbH", country="AT")
    db_session.add_all([sku, dealer_a, dealer_b, dealer_c])
    db_session.flush()

    expires = datetime.now(timezone.utc) + timedelta(hours=48)
    db_session.add_all([
        ForeignTradeInventoryReservation(
            dealer_id=dealer_a.id,
            sku_id=sku.id,
            quantity=Decimal("30"),
            reservation_kind="quote",
            reference_no="Q-A",
            expires_at=expires,
            status="active",
        ),
        ForeignTradeInventoryReservation(
            dealer_id=dealer_b.id,
            sku_id=sku.id,
            quantity=Decimal("20"),
            reservation_kind="quote",
            reference_no="Q-B",
            expires_at=expires,
            status="active",
        ),
        ForeignTradeInventoryReservation(
            dealer_id=dealer_a.id,
            sku_id=sku.id,
            quantity=Decimal("10"),
            reservation_kind="quote",
            reference_no="Q-A-EXPIRED",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            status="active",
        ),
    ])
    db_session.flush()

    monkeypatch.setattr(
        foreign_trade_service.inventory_position_service,
        "current_positions",
        lambda _db: {"by_sku": {sku.id: Decimal("100")}},
    )

    a = foreign_trade_service.dealer_inventory(db_session, dealer_a.id, sku_code=sku.sku_code)["items"][0]
    b = foreign_trade_service.dealer_inventory(db_session, dealer_b.id, sku_code=sku.sku_code)["items"][0]
    c_row = foreign_trade_service.dealer_inventory(db_session, dealer_c.id, sku_code=sku.sku_code)["items"][0]

    assert Decimal(a["publicAvailable"]) == Decimal("50")
    assert Decimal(a["dealerReserved"]) == Decimal("30")
    assert Decimal(a["availableToDealer"]) == Decimal("80")

    assert Decimal(b["publicAvailable"]) == Decimal("50")
    assert Decimal(b["dealerReserved"]) == Decimal("20")
    assert Decimal(b["availableToDealer"]) == Decimal("70")

    assert Decimal(c_row["publicAvailable"]) == Decimal("50")
    assert Decimal(c_row["dealerReserved"]) == Decimal("0")
    assert Decimal(c_row["availableToDealer"]) == Decimal("50")


def test_b2b_reservation_rejects_overbooking(db_session, monkeypatch):
    sku = ProductSku(
        jackyun_sku_id="FT-LOCK-SKU-JKY",
        sku_code="FT-LOCK-SKU",
        sku_name="Alsvid Lock Test",
        status="active",
    )
    dealer = ForeignTradeDealer(code="FT-LOCK-DEALER", company_name="Lock Dealer GmbH", country="DE")
    db_session.add_all([sku, dealer])
    db_session.flush()

    monkeypatch.setattr(
        foreign_trade_service.inventory_position_service,
        "current_positions",
        lambda _db: {"by_sku": {sku.id: Decimal("10")}},
    )

    try:
        foreign_trade_service.create_reservation(
            db_session,
            dealer_id=dealer.id,
            sku_code=sku.sku_code,
            quantity=Decimal("11"),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
    except ValueError as exc:
        assert "库存不足" in str(exc)
    else:
        raise AssertionError("超出公共可分配库存的预留必须被拒绝")



def test_b2b_order_requires_active_dealer_and_b2c_clears_binding(client, db_session):
    dealer = ForeignTradeDealer(
        code="FT-ORDER-DEALER",
        company_name="Order Dealer GmbH",
        country="DE",
        status="active",
    )
    db_session.add(dealer)
    db_session.commit()
    db_session.refresh(dealer)

    base = {
        "channel_code": "pytest-b2b",
        "external_order_no": "FT-B2B-001",
        "business_mode": "b2b",
        "brand": "Alsvid",
        "country": "DE",
        "currency": "EUR",
        "paid_amount": "1500",
        "status": "confirmed",
        "procurement_status": "pending",
        "fulfillment_status": "pending",
        "payment_status": "paid",
    }

    missing = client.post("/api/v1/foreign-trade/orders", json=base)
    assert missing.status_code == 400
    assert "必须绑定经销商" in missing.json()["detail"]

    created = client.post(
        "/api/v1/foreign-trade/orders",
        json={**base, "dealer_id": dealer.id},
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["businessMode"] == "b2b"
    assert payload["dealerId"] == dealer.id
    assert payload["dealerName"] == "Order Dealer GmbH"

    listed = client.get("/api/v1/foreign-trade/orders", params={"business_mode": "b2b"})
    assert listed.status_code == 200
    assert any(row["id"] == payload["id"] for row in listed.json()["items"])

    changed = client.put(
        f"/api/v1/foreign-trade/orders/{payload['id']}",
        json={
            **base,
            "business_mode": "b2c",
            "dealer_id": dealer.id,
            "customer_name": "Retail Customer",
        },
    )
    assert changed.status_code == 200
    assert changed.json()["businessMode"] == "b2c"
    assert changed.json()["dealerId"] is None
    assert changed.json()["dealerName"] == ""


def test_alsvid_platform_product_crud(client):
    platforms = client.get("/api/v1/foreign-trade/products/platforms", params={"brand": "ALSVID"})
    assert platforms.status_code == 200
    platform_codes = {row["code"] for row in platforms.json()["items"]}
    assert {"FC1", "FT1", "CT1", "GT1"} <= platform_codes

    created = client.post(
        "/api/v1/foreign-trade/products",
        json={
            "brand": "alsvid",
            "platform_code": "fc1",
            "model_code": "FC1-TEST",
            "name": "折叠旗舰测试",
            "name_en": "Folding Flagship Test",
            "status": "draft",
            "countries": ["de", "AT", "de"],
            "currency": "eur",
        },
    )
    assert created.status_code == 201
    product = created.json()
    assert product["brand"] == "ALSVID"
    assert product["platformCode"] == "FC1"
    assert product["platformName"] == "折叠旗舰"
    assert product["countries"] == ["DE", "AT"]

    listed = client.get(
        "/api/v1/foreign-trade/products",
        params={"brand": "ALSVID", "platform_code": "FC1", "q": "FC1-TEST"},
    )
    assert listed.status_code == 200
    assert any(row["modelCode"] == "FC1-TEST" for row in listed.json()["items"])

    updated = client.put(
        f"/api/v1/foreign-trade/products/{product['id']}",
        json={
            "brand": "ALSVID",
            "platform_code": "FC1",
            "model_code": "FC1-TEST",
            "name": "折叠旗舰测试版",
            "name_en": "Folding Flagship Test",
            "status": "active",
            "countries": ["DE", "AT"],
            "currency": "EUR",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "active"

    deleted = client.delete(f"/api/v1/foreign-trade/products/{product['id']}")
    assert deleted.status_code == 200
