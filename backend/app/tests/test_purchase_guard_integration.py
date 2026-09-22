from types import SimpleNamespace

import pytest

from app.services import procurement_consistency, purchase_service


def test_install_purchase_guards_no_longer_replaces_runtime_functions():
    advance_before = purchase_service.advance_status
    create_before = purchase_service.create_external_po

    procurement_consistency.install_purchase_guards()

    assert purchase_service.advance_status is advance_before
    assert purchase_service.create_external_po is create_before


def test_done_completion_guard_is_part_of_purchase_service(monkeypatch):
    po = SimpleNamespace(
        purchase_status="inbound",
        external_order_id="AUDIT-DONE-GUARD",
    )

    monkeypatch.setattr(
        procurement_consistency,
        "completion_snapshot",
        lambda _db, _po: {
            "complete": False,
            "issues": ["发票覆盖不足", "付款覆盖不足"],
        },
    )

    with pytest.raises(ValueError, match="不能标记完成：发票覆盖不足；付款覆盖不足"):
        purchase_service.advance_status(object(), po, "done")
