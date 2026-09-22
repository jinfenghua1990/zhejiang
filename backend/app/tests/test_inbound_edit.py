"""入库日期 / 明细含税单价的人工更正接口。

背景：入库日期决定这批成本落入哪个报告期（``weighted_inbound_costs`` 按
``document_at`` 截止取数），单价决定加权成本本身。录错都会让历史月份成本
口径失真，例如 8 月成交、9 月才登记的入库单会让 8 月报「缺采购入库成本」。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.models.catalog import ProductSku
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.models.org import AuditLog
from app.services.inbound_cost_service import weighted_inbound_costs
from app.services.inbound_edit_service import set_document_date, set_item_unit_price


def _mk_sku(db, code: str) -> ProductSku:
    sku = ProductSku(
        jackyun_sku_id=f"J-{code}", sku_code=code, sku_name=f"测试货品 {code}", unit="件",
    )
    db.add(sku)
    db.flush()
    return sku


def _mk_doc(db, *, document_at: datetime | None, total_amount: str | None = None) -> JackyunGoodsDocument:
    doc = JackyunGoodsDocument(
        document_type="inbound",
        goodsdoc_no=f"RK-EDIT-{uuid4().hex[:10]}",
        document_at=document_at,
        supplier_name="更正测试供应商",
        total_amount=Decimal(total_amount) if total_amount is not None else None,
    )
    db.add(doc)
    db.flush()
    return doc


def _mk_item(db, doc_id: int, line_no: int, sku_id: int | None, qty: str,
             price: str) -> JackyunGoodsDocumentItem:
    item = JackyunGoodsDocumentItem(
        document_id=doc_id,
        line_no=line_no,
        goods_no=f"G-{line_no}-{uuid4().hex[:6]}",
        goods_name=f"货品 {line_no}",
        quantity=Decimal(qty),
        unit_price_tax=Decimal(price),
        amount_tax=(Decimal(qty) * Decimal(price)).quantize(Decimal("0.0001")),
        matched_sku_id=sku_id,
        match_status="manual" if sku_id else "",
    )
    db.add(item)
    db.flush()
    return item


def test_set_document_date_moves_cost_into_reporting_month(db_session):
    """白糖罐案例：改入库日期后，8 月成本口径能取到 9 月登记的这批成本。"""
    sku = _mk_sku(db_session, f"EDIT-DATE-{uuid4().hex[:8]}")
    doc = _mk_doc(db_session, document_at=datetime(2026, 9, 16, 10, tzinfo=ZoneInfo(settings.TZ)))
    _mk_item(db_session, doc.id, 1, sku.id, "16", "0.7595")

    month_start = datetime(2026, 9, 1, tzinfo=ZoneInfo(settings.TZ))
    assert weighted_inbound_costs(db_session, as_of=month_start, sku_ids={sku.id}) == {}

    result = set_document_date(db_session, doc.id, datetime(2026, 8, 31, 10, 0))

    assert result["ok"] is True
    assert result["before"].startswith("2026-09-16")
    db_session.refresh(doc)
    assert doc.document_at == datetime(2026, 8, 31, 10, 0, tzinfo=ZoneInfo(settings.TZ))
    assert weighted_inbound_costs(db_session, as_of=month_start, sku_ids={sku.id}) == {
        sku.id: Decimal("0.7595")
    }


def test_set_document_date_rejects_missing_outbound_and_bad_year(db_session):
    with pytest.raises(ValueError, match="入库单不存在"):
        set_document_date(db_session, -1, datetime(2026, 8, 1))

    outbound = JackyunGoodsDocument(document_type="outbound", goodsdoc_no=f"CK-{uuid4().hex[:8]}")
    db_session.add(outbound)
    db_session.flush()
    with pytest.raises(ValueError, match="入库单不存在"):
        set_document_date(db_session, outbound.id, datetime(2026, 8, 1))

    doc = _mk_doc(db_session, document_at=datetime(2026, 9, 1))
    with pytest.raises(ValueError, match="年之间"):
        set_document_date(db_session, doc.id, datetime(1999, 8, 1))


def test_set_item_unit_price_recalculates_line_and_head(db_session):
    sku = _mk_sku(db_session, f"EDIT-PRICE-{uuid4().hex[:8]}")
    doc = _mk_doc(db_session, document_at=datetime(2026, 8, 1), total_amount="20")
    first = _mk_item(db_session, doc.id, 1, sku.id, "10", "1")
    _mk_item(db_session, doc.id, 2, None, "5", "2")

    result = set_item_unit_price(db_session, doc.id, first.id, "1.25")

    assert Decimal(result["before"]) == Decimal("1.0000")
    assert result["after"] == "1.2500"
    assert Decimal(result["amountTaxAfter"]) == Decimal("12.5000")
    assert Decimal(result["documentAmountBefore"]) == Decimal("20.0000")
    assert Decimal(result["documentAmountAfter"]) == Decimal("22.5000")
    db_session.refresh(first)
    assert first.unit_price_tax == Decimal("1.2500")
    assert first.amount_tax == Decimal("12.5000")
    db_session.refresh(doc)
    assert doc.total_amount == Decimal("22.5000")
    assert weighted_inbound_costs(db_session, sku_ids={sku.id}) == {sku.id: Decimal("1.2500")}


def test_set_item_unit_price_rejects_negative_and_foreign_item(db_session):
    sku = _mk_sku(db_session, f"EDIT-GUARD-{uuid4().hex[:8]}")
    doc = _mk_doc(db_session, document_at=datetime(2026, 8, 1))
    item = _mk_item(db_session, doc.id, 1, sku.id, "3", "2")
    other = _mk_doc(db_session, document_at=datetime(2026, 8, 1))

    with pytest.raises(ValueError, match="不能为负"):
        set_item_unit_price(db_session, doc.id, item.id, "-1")
    with pytest.raises(ValueError, match="不属于该入库单"):
        set_item_unit_price(db_session, other.id, item.id, "1")
    with pytest.raises(ValueError, match="入库单不存在"):
        set_item_unit_price(db_session, -1, item.id, "1")


def test_api_corrects_date_and_price_with_audit(db_session, client):
    sku = _mk_sku(db_session, f"EDIT-API-{uuid4().hex[:8]}")
    doc = _mk_doc(db_session, document_at=datetime(2026, 9, 16, 10, tzinfo=ZoneInfo(settings.TZ)),
                  total_amount="16")
    item = _mk_item(db_session, doc.id, 1, sku.id, "16", "1")

    response = client.patch(
        f"/api/v1/procurement-chain/inbound-documents/{doc.id}/date",
        json={"inbound_at": "2026-08-31T10:00:00", "note": "8 月成交，入库日期录错"},
    )
    assert response.status_code == 200
    assert response.json()["after"].startswith("2026-08-31")

    response = client.patch(
        f"/api/v1/procurement-chain/inbound-documents/{doc.id}/items/{item.id}/price",
        json={"unit_price_tax": "0.7595", "note": "按供应商实付单价更正"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["after"] == "0.7595"
    assert payload["amountTaxAfter"] == "12.1520"
    assert payload["documentAmountAfter"] == "12.1520"

    date_logs = db_session.query(AuditLog).filter(
        AuditLog.action == "purchase.inbound.date_correct",
        AuditLog.object_id == str(doc.id),
    ).all()
    assert len(date_logs) == 1
    assert date_logs[0].detail["after"].startswith("2026-08-31")
    price_logs = db_session.query(AuditLog).filter(
        AuditLog.action == "purchase.inbound.item_price_correct",
        AuditLog.object_id == str(item.id),
    ).all()
    assert len(price_logs) == 1
    assert price_logs[0].detail["documentAmountAfter"] == "12.1520"

    bad = client.patch(
        f"/api/v1/procurement-chain/inbound-documents/{doc.id}/date",
        json={"inbound_at": "1999-08-31T10:00:00"},
    )
    assert bad.status_code == 400