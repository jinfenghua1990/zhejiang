from types import SimpleNamespace

import pytest

from app.services import finance_service
from app.services.finance_service import DEFAULT_REQUIRED, evaluate_completeness


def f(category: str):
    return SimpleNamespace(category=category)


def test_incomplete_without_required_monthly_files():
    """新账期固定要求两份银行资料；销售汇总由系统动态生成。"""
    status, summary = evaluate_completeness([], DEFAULT_REQUIRED)
    assert status == "INCOMPLETE"
    assert summary["missing"] == {"bank": 2}


def test_ready_with_two_bank_files():
    status, summary = evaluate_completeness(
        [f("bank"), f("bank")], DEFAULT_REQUIRED
    )
    assert status == "READY"
    assert summary["missing"] == {}


def test_extra_categories_do_not_help():
    status, _ = evaluate_completeness([f("invoice"), f("other")], DEFAULT_REQUIRED)
    assert status == "INCOMPLETE"


def test_multiple_bank_files_count():
    status, summary = evaluate_completeness([f("bank"), f("bank")], {"bank": 2})
    assert status == "READY"


def test_partial_bank_shortfall():
    status, summary = evaluate_completeness([f("bank")], {"bank": 2})
    assert status == "INCOMPLETE"
    assert summary["missing"] == {"bank": 1}


def test_unknown_category_ignored():
    status, _ = evaluate_completeness([f("weird")], {"bank": 1})
    assert status == "INCOMPLETE"


@pytest.mark.parametrize("required", [{}, None])
def test_empty_required_always_ready(required):
    status, _ = evaluate_completeness([], required)
    assert status == "READY"


@pytest.mark.parametrize("year,month", [(1899, 1), (3000, 1), (2026, 0), (2026, 13)])
def test_validate_period_rejects_out_of_range_values(year, month):
    with pytest.raises(ValueError):
        finance_service.validate_period(year, month)


def test_managed_data_file_rejects_path_outside_data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    inside = data_dir / "finance" / "source.xlsx"
    inside.parent.mkdir()
    inside.write_bytes(b"inside")
    outside = tmp_path / "outside.xlsx"
    outside.write_bytes(b"outside")
    monkeypatch.setattr(finance_service.settings, "DATA_DIR", str(data_dir))

    assert finance_service.managed_data_file(inside, label="归档文件") == inside.resolve()
    with pytest.raises(RuntimeError, match="受管数据目录"):
        finance_service.managed_data_file(outside, label="归档文件")


def test_managed_data_file_rejects_external_symlink(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    finance_dir = data_dir / "finance"
    finance_dir.mkdir()
    outside = tmp_path / "outside.xlsx"
    outside.write_bytes(b"outside")
    linked = finance_dir / "linked.xlsx"
    linked.symlink_to(outside)
    monkeypatch.setattr(finance_service.settings, "DATA_DIR", str(data_dir))

    with pytest.raises(RuntimeError, match="受管数据目录"):
        finance_service.managed_data_file(linked, label="归档文件")


def test_exclusive_archive_write_never_overwrites_existing_file(tmp_path):
    target = tmp_path / "original.xlsx"
    finance_service._write_new_file(target, b"first")
    with pytest.raises(FileExistsError):
        finance_service._write_new_file(target, b"second")
    assert target.read_bytes() == b"first"


def test_store_upload_enforces_maximum_size_before_database_write(monkeypatch):
    monkeypatch.setattr(finance_service.settings, "MAX_UPLOAD_BYTES", 3)
    with pytest.raises(ValueError, match="大小上限"):
        finance_service.store_upload(
            object(), company="测试公司", year=2026, month=9, category="other",
            original_name="too-large.txt", content=b"1234",
        )



def test_finance_auto_delivery_repackages_and_sends_exact_new_version(db_session, monkeypatch):
    """自动发送必须基于发送当下资料重打包，并锁定刚生成的版本。"""
    from datetime import datetime
    from uuid import uuid4
    from zoneinfo import ZoneInfo

    from app.config import settings
    from app.models.finance import FinanceSalesReportTemplate
    from app.services import finance_sales_report_service as report_service
    from app.tasks import sync as task_sync

    now = datetime.now(ZoneInfo(settings.TZ))
    company = f"auto-delivery-{uuid4().hex}"
    template = FinanceSalesReportTemplate(
        company=company,
        name="默认财务月报",
        enabled=True,
        fields=[],
        rules={},
        to_addrs=["finance@example.com"],
        cc_addrs=[],
        auto_send=True,
        send_day=1,
        send_hour=now.hour,
    )
    db_session.add(template)
    db_session.flush()

    class SessionProxy:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            pass

    events = []
    monkeypatch.setattr(task_sync, "SessionLocal", lambda: SessionProxy(db_session))
    monkeypatch.setattr(
        report_service,
        "generate_and_archive",
        lambda *args, **kwargs: {"status": "exists"},
    )
    monkeypatch.setattr(
        finance_service,
        "refresh_period_status",
        lambda *args, **kwargs: SimpleNamespace(
            id=987654,
            status="READY",
            missing_summary={"missing": {}},
        ),
    )

    def fake_package(db, selected_company, year, month, actor="system", include=None):
        events.append(("package", selected_company, year, month, actor))
        return SimpleNamespace(version=17)

    def fake_send(
        db,
        selected_company,
        year,
        month,
        *,
        version=None,
        to_addrs=None,
        cc_addrs=None,
        actor="system",
    ):
        events.append(("send", selected_company, version, tuple(to_addrs or []), actor))
        return {"version": version, "kind": "first"}

    monkeypatch.setattr(finance_service, "package_period", fake_package)
    monkeypatch.setattr(finance_service, "send_delivery", fake_send)

    result = task_sync.finance_auto_delivery.run()

    assert [event[0] for event in events] == ["package", "send"]
    assert events[0][1] == company
    assert events[1][1] == company
    assert events[1][2] == 17
    assert events[1][3] == ("finance@example.com",)
    assert result["results"][0]["status"] == "sent"
    assert result["results"][0]["version"] == 17
