"""吉客云 MCP 业务错误解析与同步返回值回归测试。"""

import json
from types import SimpleNamespace

import httpx
import pytest

from app.adapters.base import AdapterNotConfigured, AdapterPermissionError
from app.adapters.jackyun import JackyunAdapter
from app.models.integration import IntegrationConnection, RawApiPayload
from app.services import integration_service
from app.tasks import sync


def test_nested_permission_error_is_not_treated_as_empty_success():
    inner = {
        "code": 0,
        "msg": "该应用未开通开放平台，无法调用接口，请联系客户经理处理",
        "result": {"data": None, "contextId": "ctx"},
        "subCode": "0130000609",
    }
    response = {
        "isError": False,
        "content": [{"type": "text", "text": json.dumps(inner, ensure_ascii=False)}],
    }
    payloads = JackyunAdapter._tool_payloads(response)
    assert payloads == [inner]
    with pytest.raises(AdapterPermissionError, match="0130000609"):
        JackyunAdapter._raise_for_business_error(payloads[0])


def test_multilayer_mcp_content_and_real_collection_are_unwrapped():
    response = {
        "content": [{
            "type": "text",
            "text": json.dumps({
                "isError": False,
                "content": [{
                    "type": "text",
                    "text": json.dumps([{
                        "warehouseInfo": [{"warehouseCode": "0001", "warehouseName": "主仓"}],
                    }], ensure_ascii=False),
                }],
            }, ensure_ascii=False),
        }],
    }

    payloads = JackyunAdapter._tool_payloads(response)

    assert JackyunAdapter._extract_records(payloads[0]) == [
        {"warehouseCode": "0001", "warehouseName": "主仓"},
    ]


def test_single_trade_wrapper_is_unwrapped_to_trade_rows():
    records = JackyunAdapter._extract_records({
        "data": [{"trades": [{"tradeNo": "JY001"}, {"tradeNo": "JY002"}]}],
    })

    assert records == [{"tradeNo": "JY001"}, {"tradeNo": "JY002"}]


def test_business_record_code_field_is_not_an_error():
    JackyunAdapter._raise_for_business_error(
        {"code": 0, "result": {"data": [{"code": "SKU-001", "name": "测试商品"}]}}
    )


def test_raw_payload_digest_ignores_random_jsonrpc_id():
    first = {"jsonrpc": "2.0", "id": "first", "method": "tools/call", "params": {"name": "tool", "arguments": {}}}
    second = {"jsonrpc": "2.0", "id": "second", "method": "tools/call", "params": {"name": "tool", "arguments": {}}}
    assert JackyunAdapter._request_digest(first) == JackyunAdapter._request_digest(second)


def test_raw_archive_keeps_mcp_tool_name(db_session, monkeypatch):
    body = {
        "jsonrpc": "2.0", "id": "raw-test", "method": "tools/call",
        "params": {"name": "getGoodsListInfoByGoodsNo", "arguments": {}},
    }
    response = httpx.Response(200, json={"result": {"content": []}})
    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: response)

    adapter = JackyunAdapter(db_session)
    adapter._post(body)
    row = db_session.query(RawApiPayload).filter_by(
        provider="jackyun", request_digest=JackyunAdapter._request_digest(body)
    ).one()
    assert row.method == "getGoodsListInfoByGoodsNo"

    db_session.delete(row)
    db_session.commit()


def test_blocked_connection_pauses_scheduled_sync_without_network_call():
    class FakeQuery:
        def filter_by(self, **_kwargs):
            return self

        def first(self):
            return SimpleNamespace(status="blocked")

    class FakeDb:
        def query(self, model):
            assert model is IntegrationConnection
            return FakeQuery()

    assert sync._automatic_jackyun_sync_paused(FakeDb()) is True


def test_all_sync_methods_return_stats(db_session, monkeypatch):
    adapter = JackyunAdapter(db_session)
    expected = {"fetched": 2, "created": 1, "updated": 1, "raw_stored": True}
    monkeypatch.setattr(adapter, "_fetch_and_upsert", lambda *args, **kwargs: expected)
    methods = (
        "sync_products", "sync_price_lists", "sync_sales_orders", "sync_online_orders",
        "sync_aftersales", "sync_inventory", "sync_warehouses", "sync_purchase_orders",
        "sync_purchase_settlements", "sync_purchase_returns", "sync_inbound", "sync_outbound",
        "sync_stock_allocations", "sync_shop_orders",
    )
    for name in methods:
        assert getattr(adapter, name)() == expected


@pytest.mark.parametrize(
    ("initial_status", "expected_status", "business_ready"),
    (
        ("untested", "transport_connected", False),
        ("blocked", "blocked", False),
        ("connected", "connected", True),
    ),
)
def test_transport_test_never_promotes_unverified_business_api(
    monkeypatch, initial_status, expected_status, business_ready
):
    """tools/list 成功只能证明 MCP 通道，不能伪装业务接口已经可用。"""
    conn = SimpleNamespace(
        id=1,
        status=initial_status,
        error_summary="old error",
        meta={},
        last_tested_at=None,
        last_success_at=None,
    )
    job = SimpleNamespace(id=1)

    class FakeAdapter:
        def __init__(self, _db):
            pass

        def test_connection(self):
            return {"tools": ["getGoodsListInfoByGoodsNo"], "server_info": {"name": "mcp"}}

    monkeypatch.setattr(integration_service, "get_or_create_connection", lambda *_: conn)
    monkeypatch.setattr(integration_service, "start_sync_job", lambda *_: job)
    monkeypatch.setattr(integration_service, "finish_sync_job", lambda *_: None)
    monkeypatch.setattr(integration_service, "audit", lambda *_: None)
    monkeypatch.setattr(integration_service, "JackyunAdapter", FakeAdapter)

    result = integration_service.test_jackyun(object())

    assert result["status"] == expected_status
    assert result["businessReady"] is business_ready
    assert conn.meta["transportConnected"] is True
    assert conn.meta["businessApiAvailable"] is business_ready


def test_missing_mcp_config_is_unconfigured_not_connection_error(monkeypatch):
    conn = SimpleNamespace(
        id=1,
        status="untested",
        error_summary="",
        meta={},
        last_tested_at=None,
        last_success_at=None,
    )
    job = SimpleNamespace(id=1)
    finished = []

    class FakeAdapter:
        def __init__(self, _db):
            pass

        def test_connection(self):
            raise AdapterNotConfigured("吉客云 MCP 未配置")

    class FakeDb:
        def add(self, _row):
            pass

        def commit(self):
            pass

    monkeypatch.setattr(integration_service, "get_or_create_connection", lambda *_: conn)
    monkeypatch.setattr(integration_service, "start_sync_job", lambda *_: job)
    monkeypatch.setattr(
        integration_service,
        "finish_sync_job",
        lambda _db, _job, status, *_: finished.append(status),
    )
    monkeypatch.setattr(integration_service, "audit", lambda *_: None)
    monkeypatch.setattr(integration_service, "ensure_exception", lambda *_: pytest.fail("unconfigured must not create an exception"))
    monkeypatch.setattr(integration_service, "JackyunAdapter", FakeAdapter)

    result = integration_service.test_jackyun(FakeDb())

    assert result["status"] == "unconfigured"
    assert conn.status == "unconfigured"
    assert finished == ["skipped"]


def test_goods_document_sync_persists_lines_and_is_idempotent(db_session, monkeypatch):
    from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem

    record = {
        "goodsdocNo": "TEST-IN-001",
        "inOutDate": "2026-09-02 10:00:00",
        "warehouseCode": "TEST-WH",
        "warehouseName": "测试仓",
        "companyName": "测试公司",
        "goodsDocDetailList": [
            {"goodsNo": "SKU-1", "skuBarcode": "BAR-1", "goodsName": "测试商品", "quantity": 2, "unitName": "盒"},
            {"goodsNo": "SKU-2", "skuBarcode": "BAR-2", "goodsName": "测试商品2", "quantity": 3, "unitName": "只"},
        ],
    }
    adapter = JackyunAdapter(db_session)
    monkeypatch.setattr(adapter, "ensure_configured", lambda: None)
    monkeypatch.setattr(adapter, "call_subscribed", lambda *_args, **_kwargs: {"data": [record]})

    first = adapter.sync_inbound()
    second = adapter.sync_inbound()
    row = db_session.query(JackyunGoodsDocument).filter_by(
        document_type="inbound", goodsdoc_no="TEST-IN-001"
    ).one()
    items = db_session.query(JackyunGoodsDocumentItem).filter_by(document_id=row.id).order_by(
        JackyunGoodsDocumentItem.line_no
    ).all()

    assert first["created"] == 1
    assert second["updated"] == 1
    assert str(row.total_quantity) == "5.0000"
    assert [item.quantity for item in items] == [2, 3]

    db_session.query(JackyunGoodsDocumentItem).filter_by(document_id=row.id).delete()
    db_session.delete(row)
    db_session.commit()


def test_online_order_sync_uses_real_trade_fields(db_session, monkeypatch):
    from app.models.sales import SalesOrder, SalesOrderItem

    record = {
        "tradeNo": "TEST-ONLINE-001",
        "createTime": "2026-09-02 11:00:00",
        "payTime": "2026-09-02 11:01:00",
        "shopName": "测试店铺",
        "payment": 12.5,
        "goodsDetailList": [{
            "goodsBarcode": "BAR-ONLINE",
            "goodsName": "线上测试商品",
            "sellCount": 2,
            "price": 6.25,
            "sellTotal": 12.5,
        }],
    }
    adapter = JackyunAdapter(db_session)
    monkeypatch.setattr(adapter, "ensure_configured", lambda: None)
    monkeypatch.setattr(adapter, "call_subscribed", lambda *_args, **_kwargs: {"data": [record]})

    stats = adapter.sync_online_orders()
    row = db_session.query(SalesOrder).filter_by(order_no="TEST-ONLINE-001").one()
    item = db_session.query(SalesOrderItem).filter_by(order_id=row.id).one()

    assert stats["created"] == 1
    assert row.platform == "测试店铺"
    assert str(row.paid_amount) == "12.5000"
    assert row.ordered_at is not None and row.paid_at is not None
    assert str(item.quantity) == "2.0000"

    db_session.query(SalesOrderItem).filter_by(order_id=row.id).delete()
    db_session.delete(row)
    db_session.commit()


def test_secondary_jackyun_dimensions_persist_with_external_keys(db_session, monkeypatch):
    from app.models.jackyun import (
        JackyunPurchaseReturn,
        JackyunPurchaseSettlement,
        JackyunShopOrder,
        JackyunShopOrderItem,
        JackyunStockAllocation,
    )

    responses = {
        "erp.purchordersett.get": {"data": [{
            "settNo": "TEST-SET-001", "settDate": "2026-09-02 12:00:00",
            "vendName": "测试供应商", "companyName": "测试公司",
            "totalAmount": 10, "settTotalAmount": 9, "purFee": 1, "paid": 9,
        }]},
        "erp.purchreturn.get": {"data": [{
            "orderNum": "TEST-RET-001", "purchNo": "TEST-PO-001",
            "vendName": "测试供应商", "warehouseCode": "TEST-WH",
            "warehouseName": "测试仓", "returnAmount": 4.5, "status": "done",
        }]},
        "erp.allocate.get": {"data": [{
            "allocateNo": "TEST-ALLOC-001", "createTime": "2026-09-02 13:00:00",
            "outWarehouseCode": "OUT", "inWarehouseCode": "IN", "status": "done",
        }]},
        "wms.order.query-info.page": {"data": [{
            "tradeNo": "TEST-SHOP-001", "shopName": "测试店铺",
            "createTime": "2026-09-02 14:00:00", "payTime": "2026-09-02 14:01:00",
            "goodsCount": 2, "payment": 20,
            "goodsDetailList": [{
                "platGoodsId": "PLAT-001", "goodsBarcode": "BAR-SHOP-001",
                "goodsName": "网店测试商品", "sellCount": 2, "price": 10, "sellTotal": 20,
            }],
        }]},
    }
    adapter = JackyunAdapter(db_session)
    monkeypatch.setattr(adapter, "ensure_configured", lambda: None)
    monkeypatch.setattr(adapter, "call_subscribed", lambda method, *_args, **_kwargs: responses[method])

    assert adapter.sync_purchase_settlements()["created"] == 1
    assert adapter.sync_purchase_returns()["created"] == 1
    assert adapter.sync_stock_allocations()["created"] == 1
    assert adapter.sync_shop_orders()["created"] == 1
    assert adapter.sync_shop_orders()["updated"] == 1

    settlement = db_session.query(JackyunPurchaseSettlement).filter_by(settlement_no="TEST-SET-001").one()
    purchase_return = db_session.query(JackyunPurchaseReturn).filter_by(return_no="TEST-RET-001").one()
    allocation = db_session.query(JackyunStockAllocation).filter_by(allocate_no="TEST-ALLOC-001").one()
    shop_order = db_session.query(JackyunShopOrder).filter_by(shop_order_no="TEST-SHOP-001").one()
    shop_item = db_session.query(JackyunShopOrderItem).filter_by(order_id=shop_order.id, line_no=1).one()
    assert settlement.supplier_name == "测试供应商"
    assert str(settlement.settlement_amount) == "9.0000"
    assert purchase_return.purchase_no == "TEST-PO-001"
    assert allocation.in_warehouse_code == "IN"
    assert str(shop_order.goods_count) == "2.0000"
    assert shop_item.plat_goods_id == "PLAT-001"
    assert str(shop_item.amount) == "20.0000"

    db_session.delete(settlement)
    db_session.delete(purchase_return)
    db_session.delete(allocation)
    db_session.delete(shop_item)
    db_session.delete(shop_order)
    db_session.commit()
