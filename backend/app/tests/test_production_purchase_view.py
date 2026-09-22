from datetime import datetime, timezone
from uuid import uuid4

from app.models.purchase import ExternalPurchaseOrder
from app.services.production_purchase_view import _effective_purchase_status, list_production_purchase_rows


def _po(db_session, *, suffix: str, kind: str, status: str) -> ExternalPurchaseOrder:
    row = ExternalPurchaseOrder(
        external_order_id=f"PYTEST-PROD-{suffix}-{uuid4().hex[:8]}",
        platform="1688",
        supplier_name=f"测试工厂-{suffix}",
        title=f"测试正品采购-{suffix}",
        ordered_at=datetime.now(timezone.utc),
        order_kind_override=kind,
        purchase_status=status,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def test_production_purchase_view_archives_existing_goods_and_excludes_consumables(db_session):
    producing = _po(db_session, suffix="PRODUCING", kind="goods", status="producing")
    shipped = _po(db_session, suffix="SHIPPED", kind="goods", status="shipped")
    consumable = _po(db_session, suffix="HC", kind="consumable", status="producing")

    result = list_production_purchase_rows(db_session, group="all", limit=100)
    by_no = {row["orderNo"]: row for row in result["rows"]}

    assert producing.external_order_id in by_no
    assert shipped.external_order_id in by_no
    assert consumable.external_order_id not in by_no
    assert by_no[producing.external_order_id]["stage"] == "producing"
    assert by_no[producing.external_order_id]["archiveGroup"] == "production"
    assert by_no[shipped.external_order_id]["stage"] == "shipped"
    assert by_no[shipped.external_order_id]["archiveGroup"] == "transit"

    transit = list_production_purchase_rows(db_session, group="transit", limit=100)
    transit_nos = {row["orderNo"] for row in transit["rows"]}
    assert shipped.external_order_id in transit_nos
    assert producing.external_order_id not in transit_nos
    assert consumable.external_order_id not in transit_nos

    producing_only = list_production_purchase_rows(db_session, group="production", stage="producing", limit=100)
    producing_nos = {row["orderNo"] for row in producing_only["rows"]}
    assert producing.external_order_id in producing_nos
    assert shipped.external_order_id not in producing_nos

    waiting_only = list_production_purchase_rows(db_session, group="production", stage="waiting", limit=100)
    assert producing.external_order_id not in {row["orderNo"] for row in waiting_only["rows"]}


def test_production_purchase_view_preserves_terminal_status_and_inbound_fact():
    assert _effective_purchase_status({
        "purchaseStatus": "confirmed",
        "inbound": [{"consumableUsageDecided": True}],
    }) == "inbound"

    assert _effective_purchase_status({
        "purchaseStatus": "jackyun_linked",
        "purchaseContentComplete": True,
        "allocations": [{"skuId": 1}],
        "purchaseOrders": [{"amount": 100}],
        "inbound": [{"consumableUsageDecided": True}],
        "invoice": [{"verified": False, "amount": 100}],
        "invoiceStatus": "done",
        "invoiceOutstanding": 0,
        "settlement": [{"paid": True, "paidAmount": 100}],
    }) == "done"
