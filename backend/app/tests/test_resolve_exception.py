"""故障恢复后自动关闭异常（resolve_exception）的回归测试。"""

from __future__ import annotations

from decimal import Decimal

from app.api.v1.purchase import _resync_jky_web_stockin
from app.config import settings
from app.models.ops import ExceptionRecord
from app.models.purchase import ExternalPurchaseOrder, PurchaseAllocationItem
from app.services.integration_service import ensure_exception, resolve_exception


def _pending_codes(db):
    return {
        row.code
        for row in db.query(ExceptionRecord).filter_by(status="pending").all()
    }


def test_resolve_closes_pending_and_keeps_confirmed(db_session):
    ensure_exception(db_session, "JACKYUN_SYNC_FAIL", "吉客云同步失败", "旧故障")
    # 人工确认的记录不应被自动关闭
    confirmed = (
        db_session.query(ExceptionRecord)
        .filter_by(code="JACKYUN_SYNC_FAIL", status="pending")
        .first()
    )
    confirmed.status = "confirmed"
    db_session.commit()

    closed = resolve_exception(db_session, "JACKYUN_SYNC_FAIL", "恢复自动关闭")

    assert closed == 0
    assert (
        db_session.query(ExceptionRecord)
        .filter_by(code="JACKYUN_SYNC_FAIL", status="confirmed")
        .count()
        == 1
    )


def test_resolve_closes_pending_rows_with_audit(db_session):
    ensure_exception(db_session, "ALIBABA_1688_BROWSER_CAPTURE_FAIL", "1688 捕获失败", "旧故障")
    row = (
        db_session.query(ExceptionRecord)
        .filter_by(code="ALIBABA_1688_BROWSER_CAPTURE_FAIL", status="pending")
        .first()
    )
    assert row is not None

    closed = resolve_exception(db_session, "ALIBABA_1688_BROWSER_CAPTURE_FAIL", "恢复自动关闭")

    assert closed == 1
    db_session.refresh(row)
    assert row.status == "resolved"
    assert row.handled_by == "system"
    assert row.handled_at is not None
    assert "自动关闭" in (row.note or "")
    assert "ALIBABA_1688_BROWSER_CAPTURE_FAIL" not in _pending_codes(db_session)


def test_resolve_unknown_code_is_noop(db_session):
    assert resolve_exception(db_session, "NOT_EXIST_CODE", "无此异常") == 0


def test_confirm_resync_skips_unconfigured_jky_web(monkeypatch, db_session):
    """本系统确认采购内容时，未配置吉客云同步不应被提示为失败。"""
    monkeypatch.setattr(settings, "JKY_WEB_SIGN_SECRET", "")

    result = _resync_jky_web_stockin(db_session)

    assert result["attempted"] is False
    assert result["ok"] is None
    assert "已按本系统入库流程跳过" in result["skippedReason"]

    manual_result = _resync_jky_web_stockin(db_session, required=True)
    assert manual_result["attempted"] is True
    assert manual_result["ok"] is False
    assert "JKY_WEB_SIGN_SECRET 未配置" in manual_result["error"]


def test_confirm_payment_gap_advances_purchase_content(client, db_session):
    """确认付款差异后，采购单按入库金额闭环并离开待完善。"""
    po = ExternalPurchaseOrder(
        external_order_id="EXCEPTION-PO-001",
        platform="1688",
        paid_amount=Decimal("19.13"),
        purchase_status="pending_refine",
    )
    db_session.add(po)
    db_session.flush()
    allocation = PurchaseAllocationItem(
        po_id=po.id,
        sku_code="EXCEPTION-SKU-001",
        goods_name="异常确认测试货品",
        quantity=Decimal("2"),
        unit_price=Decimal("10"),
        amount=Decimal("20"),
    )
    exception = ExceptionRecord(
        code="PURCHASE_PAYMENT_GAP",
        type="PURCHASE_PAYMENT_GAP",
        severity="medium",
        title="1688 实付与入库金额差异待确认",
        detail={"poId": po.id},
        status="pending",
        ref_table="external_purchase_orders",
        ref_id=str(po.id),
    )
    db_session.add_all([allocation, exception])
    db_session.flush()

    try:
        response = client.post(f"/api/v1/exceptions/{exception.id}/status", json={"status": "confirmed"})
        assert response.status_code == 200
        db_session.refresh(po)
        db_session.refresh(exception)
        assert po.adjustment_amount == Decimal("0.8700")
        assert po.purchase_status == "confirmed"
        assert exception.status == "confirmed"
        assert response.json()["workflow"]["purchaseStatus"] == "confirmed"
    finally:
        db_session.query(PurchaseAllocationItem).filter_by(po_id=po.id).delete(synchronize_session=False)
        db_session.delete(exception)
        db_session.delete(po)
        db_session.commit()
