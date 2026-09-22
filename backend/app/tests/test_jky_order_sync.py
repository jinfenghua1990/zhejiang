"""吉客云销售订单三通道的纯本地规范化与状态接口测试。"""

from decimal import Decimal

from app.config import settings
from app.services.jky_order_sync_service import normalize_order_rows


def test_normalize_order_rows_merges_aliases_and_keeps_items():
    rows = [
        {
            "tradeNo": "JKY-001",
            "tradeStatus": "待付款",
            "shopName": "测试店",
            "goodsDetailList": [
                {"goodsNo": "SKU-1", "goodsName": "咖啡", "sellCount": "2", "sellPrice": "3.50"},
            ],
        },
        {
            "tradeId": "JKY-001",
            "sourceTradeNo": "PLATFORM-001",
            "platform": "测试店",
            "tradeStatus": "已付款",
            "goodsDetailList": [
                {"goodsNo": "SKU-2", "goodsName": "滤纸", "sellCount": "1", "sellTotal": "5"},
            ],
        },
    ]

    orders, duplicate_count, invalid = normalize_order_rows(rows, "jky_api")

    assert invalid == []
    assert duplicate_count == 1
    assert len(orders) == 1
    assert orders[0].order_no == "JKY-001"
    assert "PLATFORM-001" in orders[0].identity_keys
    assert orders[0].order_status == "已付款"
    assert {item["sku_code"] for item in orders[0].items} == {"SKU-1", "SKU-2"}
    assert orders[0].items[0]["quantity"] == Decimal("2")


def test_normalize_order_rows_reports_rows_without_identity():
    orders, duplicate_count, invalid = normalize_order_rows(
        [{"shopName": "没有订单号", "订单状态": "已付款"}],
        "jky_web",
    )

    assert orders == []
    assert duplicate_count == 0
    assert len(invalid) == 1
    assert "缺少" in invalid[0]


def test_order_sync_status_is_read_only_and_exposes_three_channels(client):
    response = client.get("/api/v1/jky-orders/status")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["providerPriority"] == ["jky_web", "jky_rpa", "jky_api"]
    assert [channel["provider"] for channel in body["channels"]] == [
        "jky_web", "jky_rpa", "jky_api"
    ]
    assert body["lastRun"]["id"] is None or isinstance(body["lastRun"]["id"], int)


def test_order_provider_priority_is_configurable(monkeypatch):
    from app.services import jky_order_sync_service

    monkeypatch.setattr(settings, "JKY_ORDER_PROVIDER_PRIORITY", "api,web,api,unknown")

    priority, warnings = jky_order_sync_service._provider_priority()

    assert priority == ["jky_api", "jky_web"]
    assert warnings == ["忽略未知吉客云订单通道：unknown"]
