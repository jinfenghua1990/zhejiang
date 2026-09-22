from decimal import Decimal
from uuid import uuid4

from app.models.catalog import Product, ProductSku
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.models.org import AuditLog
from app.services import sku_matching_service


def _manual_match_fixture(db_session):
    suffix = uuid4().hex[:10]
    product = Product(
        jackyun_goods_id=f"goods-{suffix}",
        goods_code=f"G-{suffix}",
        goods_name="测试货品",
    )
    db_session.add(product)
    db_session.flush()
    sku = ProductSku(
        product_id=product.id,
        jackyun_sku_id=f"sku-{suffix}",
        sku_code=f"SKU-{suffix}",
        sku_name="人工指定 SKU",
        barcode=f"BAR-{suffix}",
        default_cost=Decimal("10"),
    )
    document = JackyunGoodsDocument(
        document_type="inbound",
        goodsdoc_no=f"IN-{suffix}",
        supplier_name="测试供应商",
    )
    db_session.add_all([sku, document])
    db_session.flush()
    item = JackyunGoodsDocumentItem(
        document_id=document.id,
        line_no=1,
        goods_no="不存在的货号",
        sku_barcode="不存在的条码",
        goods_name="需人工指定",
        quantity=Decimal("2"),
        amount_tax=Decimal("20"),
        matched_sku_id=sku.id,
        match_status="manual",
        match_note="人工已确认",
    )
    db_session.add(item)
    db_session.flush()
    return sku, item


def test_auto_match_never_overwrites_manual_result(db_session):
    sku, item = _manual_match_fixture(db_session)

    stats = sku_matching_service.match_inbound_items(db_session)
    db_session.refresh(item)

    assert stats["manual"] >= 1
    assert item.matched_sku_id == sku.id
    assert item.match_status == "manual"
    assert item.match_note == "人工已确认"
    summary = sku_matching_service.inbound_match_summary(db_session)
    assert any(row["itemId"] == item.id for row in summary["manualMatches"])


def test_auto_match_uses_inbound_amount_as_fact_not_fixed_catalog_cost(db_session):
    sku, item = _manual_match_fixture(db_session)
    item.goods_no = sku.sku_code
    item.sku_barcode = sku.sku_code
    item.match_status = ""
    item.match_note = ""
    item.amount_tax = Decimal("30")  # 15 / 件；档案 default_cost 仍是 10
    db_session.flush()

    stats = sku_matching_service.match_inbound_items(db_session)
    db_session.refresh(item)

    assert stats["price_ok"] >= 1
    assert stats["price_mismatch"] == 0
    assert item.matched_sku_id == sku.id
    assert item.match_status == "price_ok"
    assert "实际成本事实" in item.match_note


def test_manual_match_endpoint_can_change_existing_result(client, db_session):
    first_sku, item = _manual_match_fixture(db_session)
    suffix = uuid4().hex[:10]
    replacement = ProductSku(
        jackyun_sku_id=f"replacement-{suffix}",
        sku_code=f"R-{suffix}",
        sku_name="替换 SKU",
    )
    db_session.add(replacement)
    db_session.flush()

    response = client.post(
        f"/api/v1/purchase/sku-matching/inbound/{item.id}/manual",
        json={"sku_id": replacement.id},
    )

    assert response.status_code == 200
    db_session.refresh(item)
    assert item.matched_sku_id == replacement.id
    assert item.matched_sku_id != first_sku.id
    assert item.match_status == "manual"
    assert db_session.query(AuditLog).filter(
        AuditLog.action == "purchase.sku_match.manual",
        AuditLog.object_id == str(item.id),
    ).count() == 1


def test_manual_unmatch_stays_locked_during_auto_scan(client, db_session):
    sku, item = _manual_match_fixture(db_session)

    response = client.delete(f"/api/v1/purchase/sku-matching/inbound/{item.id}/manual")

    assert response.status_code == 200
    db_session.refresh(item)
    assert item.matched_sku_id is None
    assert item.match_status == "manual"
    assert item.match_note == "人工解除 SKU 匹配，待重新指定"
    sku_matching_service.match_inbound_items(db_session)
    db_session.refresh(item)
    assert item.matched_sku_id is None
    assert item.match_status == "manual"
    assert db_session.query(AuditLog).filter(
        AuditLog.action == "purchase.sku_match.unlinked",
        AuditLog.object_id == str(item.id),
    ).count() == 1


def test_accept_inbound_cost_is_explicit_and_audited(client, db_session):
    sku, item = _manual_match_fixture(db_session)
    item.amount_tax = Decimal("30")
    item.match_status = "price_mismatch"
    item.match_note = "金额异常，待人工确认"
    db_session.flush()

    response = client.post(
        f"/api/v1/purchase/sku-matching/inbound/{item.id}/accept-cost",
        json={"confirm": True},
    )

    assert response.status_code == 200
    assert Decimal(response.json()["oldCost"]) == Decimal("10")
    assert Decimal(response.json()["newCost"]) == Decimal("15")
    db_session.refresh(sku)
    assert sku.default_cost == Decimal("15.0000")
    assert db_session.query(AuditLog).filter(
        AuditLog.action == "purchase.sku_cost.accept",
        AuditLog.object_id == str(sku.id),
    ).count() == 1


def test_accept_inbound_cost_requires_explicit_confirmation(client, db_session):
    sku, item = _manual_match_fixture(db_session)
    item.amount_tax = Decimal("30")
    item.match_status = "price_mismatch"
    db_session.flush()

    response = client.post(f"/api/v1/purchase/sku-matching/inbound/{item.id}/accept-cost")

    assert response.status_code == 400
    assert response.json()["detail"] == "请明确确认成本变更后再提交"
    db_session.refresh(sku)
    assert sku.default_cost == Decimal("10")


def test_accept_inbound_cost_blocks_historical_outlier(client, db_session):
    sku, item = _manual_match_fixture(db_session)
    item.amount_tax = Decimal("200")
    item.match_status = "price_mismatch"
    suffix = uuid4().hex[:10]
    for line_no in (1, 2):
        history_doc = JackyunGoodsDocument(
            document_type="inbound",
            goodsdoc_no=f"HISTORY-{suffix}-{line_no}",
        )
        db_session.add(history_doc)
        db_session.flush()
        db_session.add(JackyunGoodsDocumentItem(
            document_id=history_doc.id,
            line_no=line_no,
            goods_no=f"HISTORY-{suffix}",
            goods_name="历史正常成本",
            quantity=Decimal("2"),
            amount_tax=Decimal("20"),
            matched_sku_id=sku.id,
            match_status="manual",
        ))
    db_session.flush()

    response = client.post(
        f"/api/v1/purchase/sku-matching/inbound/{item.id}/accept-cost",
        json={"confirm": True},
    )

    assert response.status_code == 400
    assert "超过 50%" in response.json()["detail"]
    db_session.refresh(sku)
    assert sku.default_cost == Decimal("10")


def test_batch_accept_cost_uses_request_actor_and_audits_each_row(client, db_session):
    sku, first = _manual_match_fixture(db_session)
    first.amount_tax = Decimal("30")
    first.match_status = "price_mismatch"
    second = JackyunGoodsDocumentItem(
        document_id=first.document_id,
        line_no=2,
        goods_no=first.goods_no,
        sku_barcode=first.sku_barcode,
        goods_name=first.goods_name,
        quantity=Decimal("2"),
        amount_tax=Decimal("30"),
        matched_sku_id=sku.id,
        match_status="price_mismatch",
    )
    db_session.add(second)
    db_session.flush()

    blocked = client.post(
        "/api/v1/purchase/sku-matching/inbound/batch-accept-cost",
        json={"document_id": first.document_id, "confirm": False},
    )
    assert blocked.status_code == 400
    assert blocked.json()["detail"] == "请明确确认成本变更后再提交"

    response = client.post(
        "/api/v1/purchase/sku-matching/inbound/batch-accept-cost",
        json={"document_id": first.document_id, "confirm": True},
    )

    assert response.status_code == 200
    assert response.json()["accepted"] == 2
    assert db_session.query(AuditLog).filter(
        AuditLog.action == "purchase.sku_cost.accept",
        AuditLog.object_id == str(sku.id),
    ).count() == 2
