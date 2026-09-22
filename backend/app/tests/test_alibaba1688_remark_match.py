"""1688 订单备注自动识别与精确建链测试。"""

from __future__ import annotations

from app.models.alibaba1688_import import Alibaba1688FileImport, Alibaba1688Order
from app.models.jackyun import JackyunGoodsDocument
from app.models.jky_web import JkyWebStockinOrder
from app.models.procurement_chain import ProcurementChainLink
from app.services.alibaba1688_remark_match_service import (
    classify_remark_references,
    extract_known_reference_numbers,
    extract_reference_numbers,
    run_verified_remark_match,
)


def test_extract_remark_references_from_free_text():
    remark = "已入库：RK202609040001；吉客云 RK202609050003 / JKY-IN:RK202609060004"
    assert extract_reference_numbers(remark) == [
        "RK202609040001",
        "RK202609050003",
        "RK202609060004",
    ]


def test_extract_known_reference_does_not_require_fixed_prefix():
    remark = "吉客云入库单号：JH-260908-001，后续以此单收货"
    assert extract_known_reference_numbers(remark, ["JH260908001"]) == ["JH260908001"]


def test_classify_references_requires_known_number():
    result = classify_remark_references(
        "入库单 RK202609040001，另一个 RK202609050003",
        ["rk202609040001"],
    )
    assert result["matched"] == ["rk202609040001"]
    assert result["unverified"] == ["RK202609050003"]


def test_classify_references_accepts_known_nonstandard_number():
    result = classify_remark_references(
        "吉客云入库：JH-260908-001",
        ["JH260908001"],
    )
    assert result["matched"] == ["JH260908001"]
    assert result["unverified"] == []


def test_verified_remark_match_creates_idempotent_confirmed_links(db_session):
    source = Alibaba1688FileImport(
        original_name="remark-test.xlsx",
        stored_path="remark-test",
        sha256="a" * 64,
        lifecycle="active",
        row_count=1,
        imported_order_count=1,
    )
    db_session.add(source)
    db_session.flush()
    order = Alibaba1688Order(
        import_id=source.id,
        external_order_id="REMARK-TEST-1688",
        order_remark="吉客云入库单：RK202609040001",
        raw_payload={},
    )
    document = JackyunGoodsDocument(
        document_type="inbound",
        goodsdoc_no="rk202609040001",
        supplier_name="测试供应商",
        raw={},
    )
    db_session.add_all([order, document])
    db_session.flush()

    first = run_verified_remark_match(db_session, actor="pytest")
    assert first["linked"] == 1
    link = db_session.query(ProcurementChainLink).filter_by(order_id=order.id).one()
    assert link.target_id == document.id
    assert link.confirmed is True
    assert link.match_method == "remark_exact"
    assert link.confidence == 1

    second = run_verified_remark_match(db_session, actor="pytest")
    assert second["alreadyLinked"] == 1
    assert db_session.query(ProcurementChainLink).filter_by(order_id=order.id).count() == 1


def test_verified_remark_match_materializes_exact_jky_web_stockin(db_session):
    source = Alibaba1688FileImport(
        original_name="jky-web-remark-test.xlsx",
        stored_path="jky-web-remark-test",
        sha256="b" * 64,
        lifecycle="active",
        row_count=1,
        imported_order_count=1,
    )
    db_session.add(source)
    db_session.flush()
    order = Alibaba1688Order(
        import_id=source.id,
        external_order_id="REMARK-TEST-JKY-WEB",
        order_remark="入库：RK202609050001",
        raw_payload={},
    )
    web_order = JkyWebStockinOrder(
        doc_id="jky-web-doc-1",
        goodsdoc_no="RK202609050001",
        supplier_name="Web供应商",
        raw={"goodsdocNo": "RK202609050001"},
    )
    db_session.add_all([order, web_order])
    db_session.flush()

    result = run_verified_remark_match(db_session, actor="pytest")
    assert result["linked"] == 1
    assert result["materializedJkyWeb"] == 1
    document = db_session.query(JackyunGoodsDocument).filter_by(
        document_type="inbound", goodsdoc_no="RK202609050001"
    ).one()
    assert document.raw["source"] == "jky_web"
    assert db_session.query(ProcurementChainLink).filter_by(
        order_id=order.id, target_id=document.id
    ).count() == 1


def test_verified_remark_match_links_known_nonstandard_jky_number(db_session):
    source = Alibaba1688FileImport(
        original_name="nonstandard-remark-test.xlsx",
        stored_path="nonstandard-remark-test",
        sha256="c" * 64,
        lifecycle="active",
        row_count=1,
        imported_order_count=1,
    )
    db_session.add(source)
    db_session.flush()
    order = Alibaba1688Order(
        import_id=source.id,
        external_order_id="REMARK-NONSTANDARD-1688",
        order_remark="吉客云采购入库单号：JH-260908-001",
        raw_payload={},
    )
    document = JackyunGoodsDocument(
        document_type="inbound",
        goodsdoc_no="JH260908001",
        supplier_name="测试供应商",
        raw={},
    )
    db_session.add_all([order, document])
    db_session.flush()

    result = run_verified_remark_match(db_session, actor="pytest")
    assert result["linked"] == 1
    link = db_session.query(ProcurementChainLink).filter_by(order_id=order.id).one()
    assert link.target_id == document.id
    assert link.match_method == "remark_exact"
    assert link.confirmed is True
