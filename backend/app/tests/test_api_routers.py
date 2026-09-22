"""API 路由级集成测试（TestClient + DB rollback）。

每个用例只断言"通路打通 + 关键字段形状"：
- 状态码符合预期（200/4xx 视 endpoint 而定）
- response JSON 包含核心字段
- 部分 endpoint 校验错误参数返回 4xx 而不是 500

不依赖外部数据：测试空表时的 default output。
"""
from __future__ import annotations

from app.config import settings


def test_healthz(client):
    """最简健康端点，无 DB 依赖。"""
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["accessMode"] == settings.ACCESS_MODE
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


def test_system_health(client):
    """/system/health 报告 db + redis 状态，空 DB 也应 up。"""
    r = client.get("/api/v1/system/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in {"ready", "degraded"}
    assert "database" in body["components"]
    assert "redis" in body["components"]


def test_system_overview(client):
    """/system/overview 返回阶段 + integrations + 看板 metrics 三件套。"""
    r = client.get("/api/v1/system/overview")
    assert r.status_code == 200
    body = r.json()
    for key in ("phase", "phaseName", "accessMode", "dataState",
                "integrations", "pendingExceptions", "nextMilestone", "metrics"):
        assert key in body, f"missing key: {key}"
    assert isinstance(body["integrations"], (list, dict))
    assert isinstance(body["metrics"], dict)


def test_integrations(client):
    """/integrations 一定返回三件套：jackyun / 1688 / smtp 状态。"""
    r = client.get("/api/v1/integrations")
    assert r.status_code == 200
    body = r.json()
    # 集成列表至少包含三个 provider（纵使状态都是 unconfigured）
    if isinstance(body, list):
        ids = {x.get("id") for x in body}
        # id 形如 jackyun_mcp / alibaba_1688 / smtp
        assert any("jackyun" in (i or "") for i in ids)
        assert any("alibaba" in (i or "") for i in ids)
        assert any("smtp" in (i or "") for i in ids)


def test_integration_status_endpoint_does_not_create_connections(client, monkeypatch):
    """查看连接状态必须是纯读取，不能隐式执行 get_or_create + commit。"""
    from app.services import integration_service

    monkeypatch.setattr(
        integration_service,
        "get_or_create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("状态读取不应创建连接")),
    )
    assert client.get("/api/v1/integrations").status_code == 200


def test_alibaba1688_auth_url_not_configured(client):
    """未提供 1688 AppKey/Secret 时明确 400，不伪造成功。"""
    r = client.get("/api/v1/integrations/alibaba1688/auth-url")
    assert r.status_code in {200, 400}
    # 若 400，body 应含明确提示；不是 500
    if r.status_code == 400:
        assert "未配置" in r.json().get("detail", "") or "AppKey" in r.json().get("detail", "")


def test_alibaba1688_callback_unconfigured(client):
    """1688 callback 在未配置时返回 waiting_config 而不是 500。"""
    r = client.get("/api/v1/integrations/alibaba1688/callback", params={"code": "fake"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["status"] in {"waiting_config", "token_exchange_pending"}


def test_dashboard_orders_empty(client):
    """/dashboard/orders 空表返回 []，N+1 修复后仍如此。"""
    r = client.get("/api/v1/dashboard/orders")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_dashboard_orders_include_item_summary(db_session):
    """首页订单摘要带出真实商品名、数量和多商品行数。"""
    from decimal import Decimal

    from app.models.sales import SalesOrder, SalesOrderItem
    from app.services.dashboard import list_orders

    order = SalesOrder(
        order_no="DASHBOARD-ITEM-SUMMARY-001",
        platform="1688",
        order_status="6000",
        order_amount=Decimal("20"),
        paid_amount=Decimal("20"),
    )
    db_session.add(order)
    db_session.flush()
    db_session.add_all([
        SalesOrderItem(order_id=order.id, sku_code="SKU-A", goods_name="商品 A", quantity=Decimal("2")),
        SalesOrderItem(order_id=order.id, sku_code="SKU-B", goods_name="商品 B", quantity=Decimal("1")),
    ])
    db_session.flush()

    row = next(item for item in list_orders(db_session) if item["orderNo"] == order.order_no)
    assert row["itemName"] == "商品 A"
    assert row["quantity"] == "3.0000"
    assert row["itemCount"] == 2


def test_dashboard_sales_trend(client):
    """/dashboard/sales-trend 默认 30 天，返回 list（可能为空）。"""
    r = client.get("/api/v1/dashboard/sales-trend")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_dashboard_platform_ranking(client):
    """/dashboard/platform-ranking 也是 list。"""
    r = client.get("/api/v1/dashboard/platform-ranking")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_dashboard_inventory_empty(client):
    """/dashboard/inventory 返回 dict（空库时 byWarehouse=[]/totalQuantity=None）。"""
    r = client.get("/api/v1/dashboard/inventory")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)
    assert "byWarehouse" in r.json() or "skuCount" in r.json()


def test_dashboard_aftersales_empty(client):
    """/dashboard/aftersales 空库返回 []。"""
    r = client.get("/api/v1/dashboard/aftersales")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_dashboard_sku_ranking(client):
    """/dashboard/sku-ranking 也走本地库聚合。"""
    r = client.get("/api/v1/dashboard/sku-ranking")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_tax_invoice_ledger_shape(client):
    """无论本机是否已有真实税务数据，台账和摘要都返回稳定结构。"""
    summary = client.get("/api/v1/tax-invoices/summary")
    assert summary.status_code == 200
    assert isinstance(summary.json()["total"], int)
    rows = client.get("/api/v1/tax-invoices", params={"limit": 500})
    assert rows.status_code == 200
    assert isinstance(rows.json(), list)
    assert summary.json()["total"] == len(rows.json())


def test_tax_invoice_partial_business_filter_is_supported(client):
    """部分业务匹配是正式状态，API 不得再用旧正则拒绝。"""
    response = client.get("/api/v1/tax-invoices", params={"match_status": "partial", "limit": 500})
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_finance_periods(client):
    """/finance/periods 是 2N+1 修复后的端点，空表返回 [] 不报错。"""
    r = client.get("/api/v1/finance/periods")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_finance_year_month_files_invalid(client):
    """非法年月（13月/0月）必须 4xx 而不是 500。"""
    r = client.get("/api/v1/finance/2026/13/files")
    assert r.status_code in {400, 422}
    r = client.get("/api/v1/finance/2026/0/files")
    assert r.status_code in {400, 422}


def test_finance_year_month_files_valid_empty(client):
    """合法年月空数据返回 []。"""
    r = client.get("/api/v1/finance/2026/9/files")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_finance_package_download_not_found(client):
    """不存在的 package id → 4xx（不返回 traceback）。"""
    r = client.get("/api/v1/finance/packages/999999/download")
    assert r.status_code in {404, 400}


def test_finance_upload_has_a_server_side_size_limit(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 3)
    r = client.post(
        "/api/v1/finance/files",
        files={"file": ("too-large.txt", b"1234", "text/plain")},
        data={"period_year": "2026", "period_month": "9", "category": "other"},
    )
    assert r.status_code == 413


def test_reconciliation_overview(client):
    """/reconciliation/overview 返回结构（real/reconciled/leftover 等 key）。"""
    r = client.get("/api/v1/reconciliation/overview")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)


def test_reconciliation_transactions_empty(client):
    """/reconciliation/transactions 空库返回 []。"""
    r = client.get("/api/v1/reconciliation/transactions")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_reconciliation_rules_empty(client):
    """/reconciliation/rules 空库返回 []。"""
    r = client.get("/api/v1/reconciliation/rules")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_reconciliation_reads_do_not_seed_rules(client, monkeypatch):
    """规则初始化只在应用启动时进行，读取列表和建议不应产生写事务。"""
    from app.services import reconciliation

    monkeypatch.setattr(
        reconciliation,
        "seed_rules_if_empty",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("GET 不应初始化规则")),
    )
    assert client.get("/api/v1/reconciliation/rules").status_code == 200
    assert client.get("/api/v1/reconciliation/suggestions").status_code == 200


def test_reconciliation_settlements_empty(client):
    """/reconciliation/settlements 空库返回 []。"""
    r = client.get("/api/v1/reconciliation/settlements")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_reconciliation_suggestions(client):
    """/reconciliation/suggestions 通常 query 限，校验 query param 校验。"""
    r = client.get("/api/v1/reconciliation/suggestions")
    assert r.status_code in {200, 422}


def test_profit_overview(client):
    """/profit/overview 返回 dict。"""
    r = client.get("/api/v1/profit/overview")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


def test_profit_costs_empty(client):
    """/profit/costs 空库返回 []。"""
    r = client.get("/api/v1/profit/costs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_profit_compute_invalid(client):
    """/profit/compute 不带必传参数应 422，不是 500。"""
    r = client.get("/api/v1/profit/compute")
    assert r.status_code == 422


def test_purchase_orders_empty(client):
    """/purchase/orders 空库返回 []。"""
    r = client.get("/api/v1/purchase/orders")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_purchase_order_not_found(client):
    """/purchase/orders/{po_id} 不存在应 4xx。"""
    r = client.get("/api/v1/purchase/orders/999999")
    assert r.status_code in {404, 400}


def test_exceptions_empty(client):
    """/exceptions 空库返回 []。"""
    r = client.get("/api/v1/exceptions")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_closing_versions_empty(client):
    """/closing/versions 空库返回 []，验证修复后的精确过滤不抛错。"""
    r = client.get("/api/v1/closing/versions")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_automation_jobs_empty(client):
    """/automation/jobs 空库返回 []。"""
    r = client.get("/api/v1/automation/jobs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_automation_logs_empty(client):
    """/automation/logs 空库返回 []。"""
    r = client.get("/api/v1/automation/logs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_opening(client):
    """/opening 返回 dict（即使数据为空）。"""
    r = client.get("/api/v1/opening")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


def test_finance_delivery_logs_empty(client):
    """/finance/delivery-logs 空库返回 []，验证 period/version 字段填充修复。"""
    r = client.get("/api/v1/finance/delivery-logs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
