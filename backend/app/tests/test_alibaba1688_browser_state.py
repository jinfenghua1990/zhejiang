"""1688 浏览器适配器登录态备份（storageState）导入/导出测试（Fake context，不开浏览器）。"""

from __future__ import annotations

import json

from app.adapters.alibaba1688_browser import Alibaba1688BrowserAdapter, _classify_mtop_ret
from app.config import settings


def test_classify_mtop_ret_accepts_list_format():
    # 实测订单接口 ret 为列表；str(list) 会破坏 startswith 匹配，必须展平判断
    assert _classify_mtop_ret(["SUCCESS::调用成功"]) == "success"
    assert _classify_mtop_ret(["FAIL_SYS_SESSION_EXPIRED::Session过期"]) == "login_expired"
    assert _classify_mtop_ret(["RGV587_ERROR:风控"]) == "risk_control"
    assert _classify_mtop_ret(["FAIL_SYS_ILLEGAL_ACCESS"]) == "risk_control"


def test_classify_mtop_ret_accepts_string_format():
    assert _classify_mtop_ret("SUCCESS::调用成功") == "success"
    assert _classify_mtop_ret("FAIL_SYS_SESSION_EXPIRED::Session过期") == "login_expired"


def test_classify_mtop_ret_empty_is_success():
    # 与原逻辑保持一致：ret 缺失/空串防御性视为成功
    assert _classify_mtop_ret("") == "success"
    assert _classify_mtop_ret(None) == "success"
    assert _classify_mtop_ret([]) == "success"


def test_classify_mtop_ret_unknown():
    assert _classify_mtop_ret(["SOME_OTHER_CODE"]) == "unknown"


class _FakeContext:
    def __init__(self) -> None:
        self.added_cookies: list[dict] = []
        self.storage_state_path: str | None = None

    def add_cookies(self, cookies):
        self.added_cookies.extend(cookies)

    def storage_state(self, *, path):
        self.storage_state_path = path
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"cookies": [{"name": "cookie2", "value": "v2"}], "origins": []}, fh)


def _adapter_with_context(monkeypatch, tmp_path) -> tuple[Alibaba1688BrowserAdapter, _FakeContext]:
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    adapter = Alibaba1688BrowserAdapter()
    context = _FakeContext()
    adapter._context = context
    return adapter, context


def test_import_state_cookies_reads_backup(monkeypatch, tmp_path):
    adapter, context = _adapter_with_context(monkeypatch, tmp_path)
    state_file = tmp_path / "alibaba1688-browser-state.json"
    state_file.write_text(
        json.dumps({"cookies": [{"name": "cookie2", "value": "v1", "domain": ".1688.com"}]}),
        encoding="utf-8",
    )
    adapter._import_state_cookies()
    assert context.added_cookies == [{"name": "cookie2", "value": "v1", "domain": ".1688.com"}]


def test_import_state_cookies_missing_file_is_noop(monkeypatch, tmp_path):
    adapter, context = _adapter_with_context(monkeypatch, tmp_path)
    adapter._import_state_cookies()
    assert context.added_cookies == []


def test_import_state_cookies_corrupt_file_is_noop(monkeypatch, tmp_path):
    adapter, context = _adapter_with_context(monkeypatch, tmp_path)
    (tmp_path / "alibaba1688-browser-state.json").write_text("not-json", encoding="utf-8")
    adapter._import_state_cookies()  # 不应抛异常
    assert context.added_cookies == []


def test_export_state_writes_file_with_600(monkeypatch, tmp_path):
    adapter, context = _adapter_with_context(monkeypatch, tmp_path)
    path = adapter.export_state()
    assert path == str(tmp_path / "alibaba1688-browser-state.json")
    assert context.storage_state_path == path
    data = json.loads((tmp_path / "alibaba1688-browser-state.json").read_text(encoding="utf-8"))
    assert data["cookies"][0]["name"] == "cookie2"
    oct_mode = (tmp_path / "alibaba1688-browser-state.json").stat().st_mode & 0o777
    assert oct_mode == 0o600


def test_export_state_without_context_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    adapter = Alibaba1688BrowserAdapter()
    assert adapter.export_state() is None
