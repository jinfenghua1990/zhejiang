"""1688 浏览器直采编排服务测试（Fake 适配器 + 空锁，不启动真实浏览器/Redis）。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.adapters.alibaba1688_browser import BrowserLoginExpiredError
from app.config import settings
from app.models.alibaba1688_import import Alibaba1688FileImport, Alibaba1688Order
from app.models.integration import IntegrationConnection
from app.services import alibaba1688_browser_sync_service as svc


def _response(orders: list[dict]) -> dict:
    return {
        "api": "mtop.1688.trading.dataline.service",
        "ret": ["SUCCESS::调用成功"],
        "data": {"result": {"orderList": orders}},
    }


def _order(
    order_id: str,
    status: str = "已发货",
    order_time: int | str = 1759977600000,
    seller_company: str = "供应商A",
    pay_time: str = "2026-09-04 10:00:00",
) -> dict:
    return {
        "orderId": order_id,
        "statusText": status,
        "sellerCompanyInfo": {"companyName": seller_company},
        "sellerNick": "sellerA",
        "buyerCompany": "买家公司",
        "buyerMember": "buyerA",
        "goodsTotal": "100.50",
        "freight": "6.00",
        "discount": "1.50",
        "actualPayment": "105.00",
        "orderTime": order_time,
        "payTime": pay_time,
    }


class _NullLock:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeAdapter:
    """替代真实 Playwright 适配器；pages 每项是一页 mtop 响应列表。"""

    def __init__(self) -> None:
        self.pages: list[list[dict]] = []
        self.logged_in = True
        self.account = "测试账号"
        self.login_result = None  # run_login 用
        self.raw_responses: list[dict] = []
        self.state_exported = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def check_login(self):
        if not self.logged_in:
            raise BrowserLoginExpiredError("页面跳转到登录页")
        return True, self.account

    def wait_for_login(self, timeout_s=None):
        return self.login_result

    def export_state(self):
        self.state_exported = True
        return "/tmp/fake-state.json"

    def iter_order_pages(self, *, max_pages=None):
        for page in self.pages:
            self.raw_responses.extend(page)
            yield page


@pytest.fixture()
def fake_adapter(monkeypatch, tmp_path):
    adapter = _FakeAdapter()
    monkeypatch.setattr(svc, "Alibaba1688BrowserAdapter", lambda: adapter)
    monkeypatch.setattr(svc, "_ProfileLock", _NullLock)
    monkeypatch.setattr(settings, "ALIBABA_1688_BROWSER_ENABLED", True)
    monkeypatch.setattr(settings, "ALIBABA_1688_BROWSER_CAPTURE_ONLY", False)
    # 正常同步现在也会归档原始 mtop 响应，测试不得写入部署机 DATA_DIR。
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    return adapter


def _conn(db):
    return (
        db.query(IntegrationConnection)
        .filter_by(provider=svc.PROVIDER, mode=svc.CONNECTION_MODE)
        .first()
    )


def test_sync_creates_orders_and_virtual_batch(db_session, fake_adapter):
    fake_adapter.pages = [[_response([_order("B1001"), _order("B1002")])]]
    result = svc.sync_orders(db_session, actor="pytest")
    assert result["status"] == "success"
    assert result["stats"]["created"] == 2
    batch = (
        db_session.query(Alibaba1688FileImport)
        .filter(Alibaba1688FileImport.stored_path == svc.VIRTUAL_BATCH_PATH_MARKER)
        .order_by(Alibaba1688FileImport.id.desc())
        .first()
    )
    assert batch is not None
    assert batch.lifecycle == "active"
    assert batch.imported_order_count == 2
    assert batch.original_name.startswith("浏览器直采 ")
    order = db_session.query(Alibaba1688Order).filter_by(external_order_id="B1001").one()
    assert order.import_id == batch.id
    assert order.seller_company_name == "供应商A"
    assert order.raw_payload.get("source") == "alibaba1688_browser"
    assert _conn(db_session).status == "connected"


def test_sync_login_required_when_expired(db_session, fake_adapter):
    fake_adapter.logged_in = False
    result = svc.sync_orders(db_session, actor="pytest")
    assert result["status"] == "login_required"
    assert _conn(db_session).status == "needs_login"
    # 不应创建虚拟批次/订单
    assert (
        db_session.query(Alibaba1688Order)
        .filter(Alibaba1688Order.external_order_id.like("B%"))
        .count()
        == 0
    )


def test_sync_skips_deleted_order_in_incremental_mode(db_session, fake_adapter):
    old_import = Alibaba1688FileImport(
        original_name="deleted-source.xlsx", stored_path="deleted-source",
        sha256="d" * 64, lifecycle="active",
    )
    db_session.add(old_import)
    db_session.flush()
    deleted = Alibaba1688Order(
        external_order_id="DELETED-DEFAULT",
        import_id=old_import.id,
        row_status="deleted",
    )
    db_session.add(deleted)
    db_session.commit()
    fake_adapter.pages = [[_response([_order("DELETED-DEFAULT")])]]

    result = svc.sync_orders(db_session, actor="pytest")

    assert result["status"] == "success"
    assert result["stats"]["skippedDeleted"] == 1
    assert result["stats"]["created"] == 0
    assert result["stats"]["restored"] == 0
    db_session.refresh(deleted)
    assert deleted.row_status == "deleted"


def test_sync_skips_closed_order_in_incremental_mode(db_session, fake_adapter):
    fake_adapter.pages = [[_response([
        _order("CLOSED-DEFAULT", status="交易关闭"),
        _order("OPEN-DEFAULT", status="待付款"),
    ])]]

    result = svc.sync_orders(db_session, actor="pytest")

    assert result["status"] == "success"
    assert result["stats"]["skippedClosed"] == 1
    assert result["stats"]["created"] == 1
    assert db_session.query(Alibaba1688Order).filter_by(
        external_order_id="CLOSED-DEFAULT"
    ).count() == 0
    assert db_session.query(Alibaba1688Order).filter_by(
        external_order_id="OPEN-DEFAULT"
    ).count() == 1


def test_sync_single_skips_explicitly_requested_closed_order(db_session, fake_adapter):
    fake_adapter.pages = [[_response([_order("CLOSED-SINGLE", status="交易关闭")])]]

    result = svc.sync_orders(
        db_session,
        actor="pytest",
        mode="single",
        order_no="CLOSED-SINGLE",
    )

    assert result["status"] == "closed_skipped"
    assert result["stats"]["stopReason"] == "target_closed"
    assert result["stats"]["skippedClosed"] == 1
    assert result["stats"]["closedStatus"] == "交易关闭"
    assert db_session.query(Alibaba1688Order).filter_by(
        external_order_id="CLOSED-SINGLE"
    ).count() == 0
    assert db_session.query(Alibaba1688FileImport).filter(
        Alibaba1688FileImport.stored_path == svc.VIRTUAL_BATCH_PATH_MARKER
    ).count() == 0


def test_sync_single_restores_explicitly_requested_deleted_order(db_session, fake_adapter):
    old_import = Alibaba1688FileImport(
        original_name="deleted-single-source.xlsx", stored_path="deleted-single-source",
        sha256="e" * 64, lifecycle="active",
    )
    db_session.add(old_import)
    db_session.flush()
    deleted = Alibaba1688Order(
        external_order_id="DELETED-SINGLE",
        import_id=old_import.id,
        row_status="deleted",
    )
    db_session.add(deleted)
    db_session.commit()
    fake_adapter.pages = [[_response([_order("DELETED-SINGLE")])]]

    result = svc.sync_orders(
        db_session,
        actor="pytest",
        mode="single",
        order_no="DELETED-SINGLE",
    )

    assert result["status"] == "success"
    assert result["stats"]["restored"] == 1
    assert result["stats"]["created"] == 0
    assert result["stats"]["stopReason"] == "target_found"
    db_session.refresh(deleted)
    assert deleted.row_status == "active"
    assert deleted.import_id == result["stats"]["batchId"]


def test_sync_incremental_stop_by_known_threshold(db_session, fake_adapter, monkeypatch):
    monkeypatch.setattr(settings, "ALIBABA_1688_BROWSER_STOP_AFTER_KNOWN", 2)
    monkeypatch.setattr(settings, "ALIBABA_1688_BROWSER_MAX_PAGES", 10)
    # 预置两条已知订单（直接造来源行，模拟历史同步结果）
    seed_import = Alibaba1688FileImport(
        original_name="seed.xlsx", stored_path="seed", sha256="seedseed" * 8,
        lifecycle="active", row_count=2, imported_order_count=2,
    )
    db_session.add(seed_import)
    db_session.flush()
    for oid in ("B0001", "B0002"):
        db_session.add(Alibaba1688Order(
            import_id=seed_import.id, external_order_id=oid,
            order_status="已发货", raw_payload={},
        ))
    db_session.flush()

    # 第 1 页：新订单 B1001 打断连续计数；第 2 页：两条已知 → 触发阈值停止；
    # 第 3 页虽然适配器还会给，但服务必须提前停止（B2001 不得入库）。
    fake_adapter.pages = [
        [_response([_order("B0001"), _order("B0002"), _order("B1001")])],
        [_response([_order("B0001"), _order("B0002")])],
        [_response([_order("B2001")])],
    ]
    result = svc.sync_orders(db_session, actor="pytest")
    assert result["status"] == "success"
    assert result["stats"]["stopReason"] == "known_threshold"
    assert result["stats"]["created"] == 1  # 只有 B1001
    assert result["stats"]["merged"] == 4   # B0001/B0002 两页各刷新两次
    assert db_session.query(Alibaba1688Order).filter_by(external_order_id="B2001").count() == 0


def test_sync_uses_lookback_window_when_known_threshold_is_not_reached(
    db_session, fake_adapter, monkeypatch
):
    monkeypatch.setattr(settings, "ALIBABA_1688_BROWSER_STOP_AFTER_KNOWN", 99)
    monkeypatch.setattr(settings, "ALIBABA_1688_BROWSER_LOOKBACK_DAYS", 1)
    fake_adapter.pages = [
        [_response([_order("B1001")])],
        [_response([_order("B1001")])],
        [_response([_order("B2001")])],
    ]
    result = svc.sync_orders(db_session, actor="pytest")
    assert result["status"] == "success"
    assert result["stats"]["stopReason"] == "lookback_window"
    assert db_session.query(Alibaba1688Order).filter_by(external_order_id="B2001").count() == 0


def test_sync_updates_existing_order_status(db_session, fake_adapter):
    fake_adapter.pages = [[_response([_order("B1001", status="已发货")])]]
    svc.sync_orders(db_session, actor="pytest")
    order = db_session.query(Alibaba1688Order).filter_by(external_order_id="B1001").one()
    assert order.order_status == "已发货"

    # 第二次同步时订单状态流转为交易成功：来源行应刷新（update_status=True）
    fake_adapter.pages = [[_response([_order("B1001", status="交易成功")])]]
    result = svc.sync_orders(db_session, actor="pytest")
    assert result["status"] == "success"
    assert result["stats"]["merged"] == 1
    assert result["stats"]["duplicates"] == 1
    db_session.refresh(order)
    assert order.order_status == "交易成功"


def test_sync_range_filters_orders_by_order_date(db_session, fake_adapter):
    fake_adapter.pages = [[_response([
        _order("R-IN", order_time="2026-09-10 10:00:00"),
        _order("R-OUT", order_time="2026-09-12 10:00:00"),
    ])]]
    result = svc.sync_orders(
        db_session,
        actor="pytest",
        mode="range",
        start_date="2026-09-09",
        end_date="2026-09-11",
    )

    assert result["status"] == "success"
    assert result["stats"]["mode"] == "range"
    assert result["stats"]["skippedOutOfWindow"] == 1
    assert db_session.query(Alibaba1688Order).filter_by(external_order_id="R-IN").count() == 1
    assert db_session.query(Alibaba1688Order).filter_by(external_order_id="R-OUT").count() == 0


def test_sync_single_only_writes_requested_order(db_session, fake_adapter):
    fake_adapter.pages = [
        [_response([_order("S-OTHER"), _order("S-TARGET")])],
        [_response([_order("S-LATER")])],
    ]
    result = svc.sync_orders(
        db_session,
        actor="pytest",
        mode="single",
        order_no="S-TARGET",
    )

    assert result["status"] == "success"
    assert result["stats"]["mode"] == "single"
    assert result["stats"]["stopReason"] == "target_found"
    assert result["stats"]["skippedNonTarget"] == 1
    assert db_session.query(Alibaba1688Order).filter_by(external_order_id="S-TARGET").count() == 1
    assert db_session.query(Alibaba1688Order).filter_by(external_order_id="S-OTHER").count() == 0
    assert db_session.query(Alibaba1688Order).filter_by(external_order_id="S-LATER").count() == 0


def test_sync_filters_by_supplier_and_payment_time(db_session, fake_adapter):
    fake_adapter.pages = [[_response([
        _order(
            "SUP-PAY-IN",
            seller_company="目标供应商有限公司",
            order_time="2026-09-01 10:00:00",
            pay_time="2026-09-10 10:00:00",
        ),
        _order(
            "SUP-PAY-OUT",
            seller_company="目标供应商有限公司",
            order_time="2026-09-10 10:00:00",
            pay_time="2026-09-12 10:00:00",
        ),
        _order(
            "SUP-WRONG",
            seller_company="其他供应商",
            order_time="2026-09-10 10:00:00",
            pay_time="2026-09-10 10:00:00",
        ),
    ])]]

    result = svc.sync_orders(
        db_session,
        actor="pytest",
        mode="range",
        start_date="2026-09-09",
        end_date="2026-09-11",
        supplier="目标供应商",
        time_field="pay_time",
    )

    assert result["status"] == "success"
    assert result["stats"]["timeField"] == "pay_time"
    assert result["stats"]["supplier"] == "目标供应商"
    assert result["stats"]["skippedOutOfWindow"] == 1
    assert result["stats"]["skippedSupplier"] == 1
    assert db_session.query(Alibaba1688Order).filter_by(
        external_order_id="SUP-PAY-IN"
    ).count() == 1
    assert db_session.query(Alibaba1688Order).filter(
        Alibaba1688Order.external_order_id.in_(["SUP-PAY-OUT", "SUP-WRONG"])
    ).count() == 0


def test_sync_single_reports_not_found_without_creating_batch(db_session, fake_adapter):
    fake_adapter.pages = [[_response([_order("S-OTHER")])]]
    result = svc.sync_orders(
        db_session,
        actor="pytest",
        mode="single",
        order_no="S-MISSING",
    )

    assert result["status"] == "not_found"
    assert result["stats"]["orderNo"] == "S-MISSING"
    assert db_session.query(Alibaba1688Order).filter_by(external_order_id="S-MISSING").count() == 0
    assert db_session.query(Alibaba1688FileImport).filter(
        Alibaba1688FileImport.stored_path == svc.VIRTUAL_BATCH_PATH_MARKER
    ).count() == 0


def test_sync_amounts_and_decimal_fields(db_session, fake_adapter):
    fake_adapter.pages = [[_response([_order("B1001")])]]
    svc.sync_orders(db_session, actor="pytest")
    order = db_session.query(Alibaba1688Order).filter_by(external_order_id="B1001").one()
    assert order.goods_total == Decimal("100.50")
    assert order.actual_payment == Decimal("105.00")


def test_run_login_success(db_session, fake_adapter):
    fake_adapter.login_result = "buyer@example.com"
    result = svc.run_login(db_session, actor="pytest")
    assert result["status"] == "success"
    conn = _conn(db_session)
    assert conn.status == "connected"
    assert conn.meta.get("account") == "buyer@example.com"
    # 登录成功必须导出登录态备份（跨启动兜底）
    assert fake_adapter.state_exported is True


def test_sync_refreshes_state_backup_when_logged_in(db_session, fake_adapter):
    fake_adapter.pages = [[_response([_order("B1001")])]]
    result = svc.sync_orders(db_session, actor="pytest")
    assert result["status"] == "success"
    assert fake_adapter.state_exported is True


def test_capture_only_dumps_samples(db_session, fake_adapter, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ALIBABA_1688_BROWSER_CAPTURE_ONLY", True)
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    fake_adapter.pages = [[_response([_order("B9999")])]]
    result = svc.sync_orders(db_session, actor="pytest")
    assert result["status"] == "capture_only"
    # 首捕模式不写订单表
    assert db_session.query(Alibaba1688Order).filter_by(external_order_id="B9999").count() == 0
    assert result["stats"]["responses"] == 1
