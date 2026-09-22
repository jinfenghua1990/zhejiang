"""利润中心单测（规格 9）：成本优先级 / 毛利诚实语义 / 多来源校验。"""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.models.catalog import ProductSku
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.models.profit import CostSnapshot
from app.models.sales import AftersalesOrder, SalesOrder, SalesOrderItem
from app.services import dashboard
from app.services.profit import compute, effective_cost, gross_profit, list_costs, upsert_cost


def _snap(**kw):
    return CostSnapshot(id=1, sku_id=1, period_year=2026, period_month=8, **kw)


def test_effective_cost_priority_actual_first():
    s = _snap(actual_cost=Decimal("10.00"), purch_order_cost=Decimal("9.00"),
              default_cost=Decimal("8.00"), estimated_cost=Decimal("7.50"))
    assert effective_cost(s) == (Decimal("10.00"), "actual_cost")


def test_effective_cost_fallback_to_estimated():
    s = _snap(actual_cost=None, purch_order_cost=None, default_cost=None,
              estimated_cost=Decimal("7.50"))
    assert effective_cost(s) == (Decimal("7.50"), "estimated_cost")


def test_effective_cost_missing():
    s = _snap()
    assert effective_cost(s) == (None, None)


def test_gross_profit():
    assert gross_profit(Decimal("100"), Decimal("70")) == Decimal("30.00")


def test_gross_profit_none_when_cost_missing():
    # 规格：成本缺失不显示假装精确的利润
    assert gross_profit(Decimal("100"), None) is None
    assert gross_profit(None, Decimal("70")) is None
    assert gross_profit(None, None) is None


def test_gross_profit_rounds_to_cents():
    assert gross_profit(Decimal("100.005"), Decimal("30")) == Decimal("70.01")


def test_upsert_rejects_multiple_sources():
    with pytest.raises(ValueError):
        upsert_cost(None, sku_id=1, period_year=2026, period_month=8,
                    actual_cost="10", default_cost="8")


def test_upsert_rejects_bad_period():
    with pytest.raises(ValueError):
        upsert_cost(None, sku_id=1, period_year=2026, period_month=13,
                    estimated_cost="8")


def test_upsert_keeps_multiple_sources_and_uses_priority(db_session):
    sku = ProductSku(jackyun_sku_id="test-profit-multi", sku_code="P-MULTI", sku_name="多来源")
    db_session.add(sku)
    db_session.commit()

    upsert_cost(db_session, sku_id=sku.id, period_year=2098, period_month=8,
                default_cost="8.00", source="default", actor="pytest-admin")
    upsert_cost(db_session, sku_id=sku.id, period_year=2098, period_month=8,
                actual_cost="10.00", source="actual", actor="pytest-admin")

    rows = list_costs(db_session, period_year=2098, period_month=8)
    row = next(item for item in rows if item["skuId"] == sku.id)
    assert row["values"]["default"] == "8.0000"
    assert row["values"]["actual"] == "10.0000"
    assert row["effectiveCost"] == "10.0000"
    assert row["effectiveSource"] == "实际采购结算成本"


def test_compute_filters_period_and_multiplies_unit_cost_by_quantity(db_session):
    sku = ProductSku(
        jackyun_sku_id="test-profit-calc", sku_code="P-CALC", sku_name="计算",
        default_cost=Decimal("5.00"),
    )
    db_session.add(sku)
    db_session.flush()
    august = SalesOrder(
        order_no="test-profit-2098-08", order_status="paid",
        paid_amount=Decimal("90"),
        ordered_at=datetime(2098, 8, 15, tzinfo=timezone.utc),
    )
    september = SalesOrder(
        order_no="test-profit-2098-09", order_status="paid",
        paid_amount=Decimal("999"),
        ordered_at=datetime(2098, 9, 1, tzinfo=timezone.utc),
    )
    db_session.add_all([august, september])
    db_session.flush()
    inbound = JackyunGoodsDocument(
        document_type="inbound", goodsdoc_no="PROFIT-CALC-INBOUND",
        document_at=datetime(2098, 7, 1, tzinfo=timezone.utc),
    )
    db_session.add(inbound)
    db_session.flush()
    db_session.add(JackyunGoodsDocumentItem(
        document_id=inbound.id, line_no=1, goods_no=sku.sku_code,
        quantity=Decimal("100"), unit_price_tax=Decimal("5.00"), matched_sku_id=sku.id,
    ))
    db_session.add_all([
        SalesOrderItem(order_id=august.id, sku_id=sku.id, quantity=Decimal("3"),
                       amount=Decimal("100"), discount_amount=Decimal("10")),
        SalesOrderItem(order_id=september.id, sku_id=sku.id, quantity=Decimal("99"),
                       amount=Decimal("999"), discount_amount=Decimal("0")),
    ])
    db_session.commit()

    result = compute(db_session, 2098, 8)
    assert result["netSales"] == "90.00"
    assert result["goodsCost"] == "15.00"
    assert result["grossProfit"] == "75.00"
    assert result["costMissing"] is False


def test_compute_prefers_weighted_inbound_cost_over_default(db_session):
    sku = ProductSku(
        jackyun_sku_id="test-profit-weighted", sku_code="P-WEIGHTED", sku_name="加权成本",
        default_cost=Decimal("99.00"),
    )
    db_session.add(sku)
    db_session.flush()
    first = JackyunGoodsDocument(
        document_type="inbound", goodsdoc_no="PROFIT-WEIGHTED-1",
        document_at=datetime(2098, 7, 1, tzinfo=timezone.utc),
    )
    second = JackyunGoodsDocument(
        document_type="inbound", goodsdoc_no="PROFIT-WEIGHTED-2",
        document_at=datetime(2098, 8, 1, tzinfo=timezone.utc),
    )
    db_session.add_all([first, second])
    db_session.flush()
    db_session.add_all([
        JackyunGoodsDocumentItem(
            document_id=first.id, line_no=1, goods_no=sku.sku_code,
            sku_barcode=sku.sku_code, quantity=Decimal("2"),
            unit_price_tax=Decimal("10"), matched_sku_id=sku.id,
        ),
        JackyunGoodsDocumentItem(
            document_id=second.id, line_no=1, goods_no=sku.sku_code,
            sku_barcode=sku.sku_code, quantity=Decimal("3"),
            unit_price_tax=Decimal("20"), matched_sku_id=sku.id,
        ),
    ])
    order = SalesOrder(
        order_no="test-profit-weighted-order", order_status="paid",
        paid_amount=Decimal("100"),
        ordered_at=datetime(2098, 8, 15, tzinfo=timezone.utc),
    )
    db_session.add(order)
    db_session.flush()
    db_session.add(SalesOrderItem(
        order_id=order.id, sku_id=sku.id, quantity=Decimal("1"),
        amount=Decimal("100"), discount_amount=Decimal("0"),
    ))
    db_session.commit()

    result = compute(db_session, 2098, 8)
    assert result["goodsCost"] == "16.00"  # (2*10 + 3*20) / 5
    assert result["grossProfit"] == "84.00"


def test_compute_uses_asia_shanghai_month_boundary(db_session):
    """2098-08-31 16:30 UTC 已是上海 9 月 1 日 00:30，必须归入 9 月。"""
    sku = ProductSku(
        jackyun_sku_id="test-profit-tz", sku_code="P-TZ", sku_name="时区边界",
        default_cost=Decimal("10.00"),
    )
    db_session.add(sku)
    db_session.flush()
    order = SalesOrder(
        order_no="test-profit-tz-boundary", order_status="paid",
        paid_amount=Decimal("50"),
        ordered_at=datetime(2098, 8, 31, 16, 30, tzinfo=timezone.utc),
    )
    db_session.add(order)
    db_session.flush()
    inbound = JackyunGoodsDocument(
        document_type="inbound", goodsdoc_no="PROFIT-TZ-INBOUND",
        document_at=datetime(2098, 7, 1, tzinfo=timezone.utc),
    )
    db_session.add(inbound)
    db_session.flush()
    db_session.add(JackyunGoodsDocumentItem(
        document_id=inbound.id, line_no=1, goods_no=sku.sku_code,
        quantity=Decimal("100"), unit_price_tax=Decimal("10.00"), matched_sku_id=sku.id,
    ))
    db_session.add(SalesOrderItem(
        order_id=order.id, sku_id=sku.id, quantity=Decimal("2"),
        amount=Decimal("50"), discount_amount=Decimal("0"),
    ))
    db_session.commit()

    august = compute(db_session, 2098, 8)
    september = compute(db_session, 2098, 9)
    assert august["netSales"] is None
    assert september["netSales"] == "50.00"
    assert september["goodsCost"] == "20.00"
    assert september["grossProfit"] == "30.00"


def test_compute_with_unmapped_item_does_not_publish_partial_cost(db_session):
    order = SalesOrder(
        order_no="test-profit-unmapped-2098", pay_status="paid",
        paid_amount=Decimal("20"),
        ordered_at=datetime(2098, 7, 10, tzinfo=timezone.utc),
    )
    db_session.add(order)
    db_session.flush()
    item = SalesOrderItem(
        order_id=order.id, sku_id=None, quantity=Decimal("1"),
        amount=Decimal("20"), discount_amount=Decimal("0"),
    )
    db_session.add(item)
    db_session.commit()

    result = compute(db_session, 2098, 7)
    assert result["netSales"] == "20.00"
    assert result["goodsCost"] is None
    assert result["grossProfit"] is None
    assert result["unmappedItems"] == [item.id]
    assert result["costMissing"] is True


def test_dashboard_cost_uses_inbound_fact_not_sales_import_cost(db_session):
    sku = ProductSku(
        jackyun_sku_id="test-dashboard-cost", sku_code="P-DASH-COST", sku_name="看板成本",
        default_cost=Decimal("99.00"),
    )
    db_session.add(sku)
    db_session.flush()
    inbound = JackyunGoodsDocument(
        document_type="inbound", goodsdoc_no="DASHBOARD-COST-INBOUND",
        document_at=datetime(2098, 7, 1, tzinfo=timezone.utc),
    )
    db_session.add(inbound)
    db_session.flush()
    db_session.add(JackyunGoodsDocumentItem(
        document_id=inbound.id, line_no=1, goods_no=sku.sku_code,
        quantity=Decimal("100"), unit_price_tax=Decimal("5.00"), matched_sku_id=sku.id,
    ))
    order = SalesOrder(
        order_no="test-dashboard-cost-order", platform="PDD", order_status="已完成",
        ordered_at=datetime(2098, 8, 15, tzinfo=timezone.utc), paid_amount=Decimal("100"),
        raw={"goodsCost": 9999},
    )
    db_session.add(order)
    db_session.flush()
    db_session.add(SalesOrderItem(
        order_id=order.id, sku_code=sku.sku_code, quantity=Decimal("2"), amount=Decimal("100"),
    ))
    db_session.commit()

    trend = dashboard.sales_trend(db_session, start=date(2098, 8, 1), end=date(2098, 8, 31))
    ranking = dashboard.platform_ranking(db_session, start=date(2098, 8, 1), end=date(2098, 8, 31))
    # 聚合接口输出 4 位小数（前端汇总后再格式化），数值需与分位一致
    assert Decimal(trend[0]["costAmount"]) == Decimal("10.00")
    assert Decimal(trend[0]["grossProfit"]) == Decimal("90.00")
    assert Decimal(ranking[0]["costAmount"]) == Decimal("10.00")
    assert Decimal(ranking[0]["grossProfit"]) == Decimal("90.00")


def test_compute_reports_partial_cost_with_warning_for_missing_sku(db_session):
    """个别 SKU 缺入库成本时毛利必须出数（按已覆盖部分），并给出可调整的告警。"""
    covered = ProductSku(jackyun_sku_id="test-profit-covered", sku_code="P-COVERED", sku_name="有成本")
    missing = ProductSku(jackyun_sku_id="test-profit-missing", sku_code="P-MISSING", sku_name="缺成本")
    db_session.add_all([covered, missing])
    db_session.flush()
    inbound = JackyunGoodsDocument(
        document_type="inbound", goodsdoc_no="PROFIT-PARTIAL-INBOUND",
        document_at=datetime(2098, 6, 1, tzinfo=timezone.utc),
    )
    db_session.add(inbound)
    db_session.flush()
    db_session.add(JackyunGoodsDocumentItem(
        document_id=inbound.id, line_no=1, goods_no=covered.sku_code,
        quantity=Decimal("10"), unit_price_tax=Decimal("4.00"), matched_sku_id=covered.id,
    ))
    order = SalesOrder(
        order_no="test-profit-partial-2098-06", order_status="已完成",
        paid_amount=Decimal("100"),
        ordered_at=datetime(2098, 6, 10, tzinfo=timezone.utc),
    )
    db_session.add(order)
    db_session.flush()
    db_session.add_all([
        SalesOrderItem(order_id=order.id, sku_id=covered.id, sku_code=covered.sku_code,
                       quantity=Decimal("2"), amount=Decimal("80")),
        SalesOrderItem(order_id=order.id, sku_id=missing.id, sku_code=missing.sku_code,
                       quantity=Decimal("1"), amount=Decimal("20")),
    ])
    db_session.commit()

    result = compute(db_session, 2098, 6)
    assert result["netSales"] == "100.00"
    assert result["goodsCost"] == "8.00"  # 只含已覆盖的 2 × 4.00
    assert result["grossProfit"] == "92.00"  # 按已覆盖部分出数，缺成本部分未计入
    assert result["costMissing"] is True
    assert result["error"] is None
    assert "P-MISSING" in (result["warning"] or "")
    assert [row["skuCode"] for row in result["costMissingDetail"]] == ["P-MISSING"]


def test_sku_ranking_allocates_order_paid_amount_by_cost_share(db_session):
    """SKU 排行按行成本占比分摊订单实付，分摊合计等于业绩口径销售额。"""
    sku_a = ProductSku(jackyun_sku_id="test-rank-a", sku_code="P-RANK-A", sku_name="A")
    sku_b = ProductSku(jackyun_sku_id="test-rank-b", sku_code="P-RANK-B", sku_name="B")
    db_session.add_all([sku_a, sku_b])
    db_session.flush()
    inbound = JackyunGoodsDocument(
        document_type="inbound", goodsdoc_no="RANK-INBOUND",
        document_at=datetime(2098, 5, 1, tzinfo=timezone.utc),
    )
    db_session.add(inbound)
    db_session.flush()
    db_session.add_all([
        JackyunGoodsDocumentItem(document_id=inbound.id, line_no=1, goods_no=sku_a.sku_code,
                                 quantity=Decimal("10"), unit_price_tax=Decimal("4.00"), matched_sku_id=sku_a.id),
        JackyunGoodsDocumentItem(document_id=inbound.id, line_no=2, goods_no=sku_b.sku_code,
                                 quantity=Decimal("10"), unit_price_tax=Decimal("6.00"), matched_sku_id=sku_b.id),
    ])
    order = SalesOrder(
        order_no="test-rank-2098-05", order_status="已完成", paid_amount=Decimal("100"),
        ordered_at=datetime(2098, 5, 20, tzinfo=timezone.utc),
    )
    db_session.add(order)
    db_session.flush()
    db_session.add_all([
        SalesOrderItem(order_id=order.id, sku_id=sku_a.id, sku_code=sku_a.sku_code,
                       goods_name="A", quantity=Decimal("2"), amount=Decimal("80")),
        SalesOrderItem(order_id=order.id, sku_id=sku_b.id, sku_code=sku_b.sku_code,
                       goods_name="B", quantity=Decimal("2"), amount=Decimal("20")),
    ])
    db_session.commit()

    rows = dashboard.sku_ranking(db_session, start=date(2098, 5, 1), end=date(2098, 5, 31))
    by_code = {row["skuCode"]: row for row in rows}
    assert Decimal(by_code["P-RANK-A"]["salesAmount"]) == Decimal("40.00")  # 100 × 8/20
    assert Decimal(by_code["P-RANK-B"]["salesAmount"]) == Decimal("60.00")  # 100 × 12/20
    assert sum(Decimal(row["salesAmount"]) for row in rows) == Decimal("100.00")

def test_profit_net_sales_uses_same_refund_formula_as_monthly_overview(db_session):
    sku = ProductSku(jackyun_sku_id="test-profit-refund", sku_code="P-REFUND", sku_name="退款口径")
    db_session.add(sku)
    db_session.flush()
    inbound = JackyunGoodsDocument(
        document_type="inbound", goodsdoc_no="PROFIT-REFUND-INBOUND",
        document_at=datetime(2098, 4, 1, tzinfo=timezone.utc),
    )
    db_session.add(inbound)
    db_session.flush()
    db_session.add(JackyunGoodsDocumentItem(
        document_id=inbound.id, line_no=1, goods_no=sku.sku_code,
        quantity=Decimal("10"), unit_price_tax=Decimal("10"), matched_sku_id=sku.id,
    ))
    order = SalesOrder(
        order_no="test-profit-refund-order", order_status="已完成",
        paid_amount=Decimal("100"), ordered_at=datetime(2098, 4, 10, tzinfo=timezone.utc),
    )
    db_session.add(order)
    db_session.flush()
    db_session.add(SalesOrderItem(
        order_id=order.id, sku_id=sku.id, sku_code=sku.sku_code,
        quantity=Decimal("2"), amount=None, discount_amount=None,
    ))
    db_session.add(AftersalesOrder(
        aftersale_no="test-profit-refund-aftersale",
        order_no=order.order_no, type="refund", status="done",
        refund_amount=Decimal("30"), created_at_src=datetime(2098, 4, 20, tzinfo=timezone.utc),
    ))
    db_session.flush()

    result = compute(db_session, 2098, 4)
    assert result["netSales"] == "70.00"
    assert result["goodsCost"] == "20.00"
    assert result["grossProfit"] == "50.00"
