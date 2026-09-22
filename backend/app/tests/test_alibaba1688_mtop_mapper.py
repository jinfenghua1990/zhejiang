"""alibaba1688_mtop_mapper 防御式映射器单元测试。"""

from __future__ import annotations

import json

from app.config import settings
from app.services.alibaba1688_mtop_mapper import (
    extract_order_id,
    extract_orders,
    map_order,
    mtop_to_datetime,
)


def _response(orders: list[dict]) -> dict:
    return {
        "api": "mtop.1688.trading.dataline.service",
        "ret": ["SUCCESS::调用成功"],
        "data": {"result": {"orderList": orders}},
    }


def _order(order_id: str = "2240001") -> dict:
    return {
        "orderId": order_id,
        "statusText": "已发货",
        "sellerCompanyInfo": {"companyName": "杭州某某实业有限公司"},
        "sellerNick": "sellerA",
        "buyerCompany": "买家公司",
        "buyerMember": "buyerA",
        "goodsTotal": "1,234.56",
        "freight": 6,
        "discount": "¥4.50",
        "actualPayment": 1236.06,
        "orderTime": 1759977600000,  # epoch 毫秒
        "payTime": "2026-09-04 10:00:00",
    }


def test_extract_orders_from_default_container():
    orders = extract_orders(_response([_order(), _order("2240002")]))
    assert len(orders) == 2
    assert orders[0]["orderId"] == "2240001"


def test_extract_orders_empty_when_no_container():
    assert extract_orders({"data": {"foo": 1}}) == []


def test_extract_orders_from_realistic_nested_json_string():
    """实测结构回归：data.data.result 是 JSON 编码字符串，解析后 data.data 为订单数组。

    2026-09-04 首捕（response_13.json，buyer-order-list 主接口）确认该嵌套形态，
    _walk 需要自动穿透 JSON 字符串。
    """
    inner_orders = [
        {
            "idStr": "5127220299021001028",
            "statusLabel": "待收货",
            "status": "waitbuyerreceive",
            "buyerInfo": {"loginId": "jinfenghua1990"},
            "sellerInfo": {
                "companyName": "森耀实业(珠海)有限公司",
                "loginId": "亮点包装制品公司",
            },
            "sumProductPayment": "400",
            "carriage": "350",
            "allPromotionFee": "0",
            "sumPayment": "750",
            "gmtCreate": "2026-09-02 09:59:41",
            "gmtPayment": 1788314408000,
        }
    ]
    inner_json = json.dumps({"data": {"data": inner_orders}}, ensure_ascii=False)
    response = {
        "api": "mtop.1688.trading.dataline.service",
        "ret": ["SUCCESS::调用成功"],
        "data": {"data": {"result": inner_json}},
    }
    orders = extract_orders(response)
    assert len(orders) == 1
    mapped = map_order(orders[0])
    assert mapped["external_order_id"] == "5127220299021001028"
    assert mapped["seller_company_name"] == "森耀实业(珠海)有限公司"
    assert mapped["buyer_member_name"] == "jinfenghua1990"
    # 金额：mtop 以分返回（400 分 = 4.00 元，750 分 = 7.50 元）
    assert str(mapped["goods_total"]) == "4.00"
    assert str(mapped["freight"]) == "3.50"
    assert str(mapped["actual_payment"]) == "7.50"
    assert mapped["order_status"] == "待收货"
    assert mapped["order_time"] is not None and mapped["order_time"].year == 2026
    assert mapped["pay_time"] is not None and mapped["pay_time"].year == 2026


def test_map_order_fields_types_and_coercion():
    data = map_order(_order())
    assert data["external_order_id"] == "2240001"
    assert data["seller_company_name"] == "杭州某某实业有限公司"
    assert data["order_status"] == "已发货"
    # 金额：纯整数按分转元（mtop 实测单位为分）；千分位/货币符号/float 按元归一
    assert str(data["goods_total"]) == "1234.56"
    assert str(data["freight"]) == "0.06"  # 6 分 -> 0.06 元
    assert str(data["discount"]) == "4.50"
    assert abs(float(data["actual_payment"]) - 1236.06) < 1e-9
    # 时间：epoch 毫秒与日期字符串都要能解析
    assert data["order_time"] is not None and data["order_time"].year == 2025
    assert data["pay_time"] is not None and data["pay_time"].hour == 10
    # 原始 dict 附带保存，供落库回溯
    assert data["_raw"]["orderId"] == "2240001"


def test_map_order_money_cents_to_yuan():
    """实测 mtop 金额为分（首捕与 Excel 比对确认）：750 分 = 7.50 元。"""
    raw = {
        "idStr": "9001",
        "sumProductPayment": "400",
        "carriage": "350",
        "allPromotionFee": "0",
        "sumPayment": "750",
    }
    data = map_order(raw)
    assert str(data["goods_total"]) == "4.00"
    assert str(data["freight"]) == "3.50"
    assert str(data["discount"]) == "0.00"
    assert str(data["actual_payment"]) == "7.50"


def test_map_order_unmapped_fields_recorded():
    data = map_order({"foo": "bar"})
    assert "external_order_id" in data["_unmapped"]
    assert data.get("external_order_id") is None or data["external_order_id"] == ""


def test_map_order_extracts_order_remark():
    raw = _order("remark-1")
    raw["remark"] = "吉客云入库单 RK202609040001"
    data = map_order(raw)
    assert data["order_remark"] == "吉客云入库单 RK202609040001"


def test_map_order_extracts_tracking_number_from_order_entries():
    raw = _order("logistics-1")
    raw["orderEntries"] = [
        {"entryExtension": {"trackingNo": "SF123"}},
        {"entryExtension": {"trackingNo": "SF123"}},
        {"entryExtension": {"trackingNo": "YT456"}},
    ]
    data = map_order(raw)
    assert data["logistics"] == {"trackingNo": "SF123、YT456"}


def test_extract_order_id_quick_path():
    assert extract_order_id(_order("998877")) == "998877"
    assert extract_order_id({"foo": 1}) == ""


def test_settings_path_override(monkeypatch):
    override = json.dumps({
        "containers": ["custom.list"],
        "fields": {"external_order_id": ["oid"], "order_status": ["state"]},
    })
    monkeypatch.setattr(settings, "ALIBABA_1688_MTOP_FIELD_PATHS_JSON", override)
    response = {"data": {"custom": {"list": [{"oid": "777", "state": "已付款"}]}}}
    orders = extract_orders(response)
    assert len(orders) == 1
    data = map_order(orders[0])
    assert data["external_order_id"] == "777"
    assert data["order_status"] == "已付款"


def test_datetime_variants():
    assert mtop_to_datetime(1759977600) is not None  # epoch 秒
    assert mtop_to_datetime("20260904") is not None  # yyyymmdd
    assert mtop_to_datetime("") is None
    assert mtop_to_datetime("not-a-date") is None
