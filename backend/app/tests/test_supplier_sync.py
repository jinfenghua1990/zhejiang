from decimal import Decimal

from app.api.v1.suppliers import list_suppliers
from app.models.purchase import ExternalPurchaseOrder, JackyunPurchaseOrder, JackyunPurchaseOrderLink, Supplier
from app.models.production import ProductionOrder
from app.services.supplier_sync_service import sync_suppliers_from_business_data


def test_sync_creates_suppliers_only_from_purchase_orders(db_session):
    db_session.add_all([
        ExternalPurchaseOrder(
            external_order_id="SUPPLIER-SYNC-PO-001",
            platform="1688",
            supplier_name="采购供应商  有限公司",
        ),
        ProductionOrder(
            order_no="SUPPLIER-SYNC-SC-001",
            factory_name="生产执行工厂",
        ),
    ])
    db_session.flush()

    result = sync_suppliers_from_business_data(db_session)

    assert result["created"] == 1
    assert result["sources"] == {"purchaseOrders": 1, "1688Orders": 0, "jackyunPurchaseOrders": 0}
    assert {row.name for row in db_session.query(Supplier).all()} == {
        "采购供应商 有限公司",
    }
    assert db_session.query(Supplier).filter_by(name="采购供应商 有限公司").one().platform == "1688"
    assert db_session.query(Supplier).filter_by(name="生产执行工厂").count() == 0


def test_sync_is_idempotent_and_skips_reference_only_purchase(db_session):
    db_session.add_all([
        ExternalPurchaseOrder(
            external_order_id="SUPPLIER-SYNC-REFERENCE-001",
            platform="other",
            supplier_name="仅供参考的供应商",
            raw={"referenceOnly": True},
        ),
        ExternalPurchaseOrder(
            external_order_id="SUPPLIER-SYNC-REAL-001",
            platform="taobao",
            supplier_name="真实供应商",
        ),
    ])
    db_session.flush()

    first = sync_suppliers_from_business_data(db_session)
    second = sync_suppliers_from_business_data(db_session)

    assert first["created"] == 1
    assert second["created"] == 0
    assert db_session.query(Supplier).filter_by(name="真实供应商").count() == 1
    assert db_session.query(Supplier).filter_by(name="仅供参考的供应商").count() == 0


def test_supplier_list_is_purchase_scoped_and_derives_purchase_type(db_session):
    db_session.add(
        Supplier(
            name="历史报销对象",
            platform="其他",
        )
    )
    db_session.add_all([
        ExternalPurchaseOrder(
            external_order_id="SUPPLIER-LIST-ONE-001",
            platform="other",
            supplier_name="一次采购供应商",
            order_amount=Decimal("100.00"),
            paid_amount=Decimal("100.00"),
        ),
        ExternalPurchaseOrder(
            external_order_id="SUPPLIER-LIST-REGULAR-001",
            platform="taobao",
            supplier_name="常购  供应商",
            order_amount=Decimal("200.00"),
            paid_amount=Decimal("200.00"),
        ),
        ExternalPurchaseOrder(
            external_order_id="SUPPLIER-LIST-REGULAR-002",
            platform="taobao",
            supplier_name="常购 供应商",
            order_amount=Decimal("300.00"),
            paid_amount=Decimal("300.00"),
        ),
    ])
    db_session.flush()

    all_rows = list_suppliers(keyword="", status="all", db=db_session)
    by_name = {row["name"]: row for row in all_rows}

    assert set(by_name) == {"一次采购供应商", "常购 供应商"}
    assert "历史报销对象" not in by_name
    assert by_name["一次采购供应商"]["orderCount"] == 1
    assert by_name["一次采购供应商"]["purchaseType"] == "temporary"
    assert by_name["常购 供应商"]["orderCount"] == 2
    assert by_name["常购 供应商"]["purchaseType"] == "regular"

    regular = list_suppliers(keyword="", status="regular", db=db_session)
    temporary = list_suppliers(keyword="", status="temporary", db=db_session)

    assert [row["name"] for row in regular] == ["常购 供应商"]
    assert [row["name"] for row in temporary] == ["一次采购供应商"]



def test_standalone_jackyun_purchase_creates_supplier_and_appears_in_supplier_list(db_session):
    jpo = JackyunPurchaseOrder(
        jackyun_purch_id="JKY-STANDALONE-001",
        purch_no="CG-2026-0001",
        supplier_name="合锦（广州）供应链有限公司",
        amount=Decimal("3080.00"),
        status="completed",
    )
    db_session.add(jpo)
    db_session.flush()

    sync = sync_suppliers_from_business_data(db_session)
    rows = list_suppliers(keyword="合锦", status="all", db=db_session)

    assert sync["sources"]["jackyunPurchaseOrders"] == 1
    assert len(rows) == 1
    assert rows[0]["name"] == "合锦(广州)供应链有限公司"
    assert rows[0]["orderCount"] == 1
    assert rows[0]["purchaseType"] == "temporary"


def test_linked_jackyun_purchase_is_not_double_counted(db_session):
    external = ExternalPurchaseOrder(
        external_order_id="EXT-JKY-001",
        platform="other",
        supplier_name="同一采购供应商",
        order_amount=Decimal("500.00"),
        paid_amount=Decimal("500.00"),
    )
    jpo = JackyunPurchaseOrder(
        jackyun_purch_id="JKY-LINKED-001",
        purch_no="CG-LINKED-001",
        supplier_name="同一采购供应商",
        amount=Decimal("500.00"),
        status="completed",
    )
    db_session.add_all([external, jpo])
    db_session.flush()
    db_session.add(
        JackyunPurchaseOrderLink(
            po_id=external.id,
            jackyun_po_id=jpo.id,
            relation_kind="",
            alloc_amount=Decimal("500.00"),
            note="",
        )
    )
    db_session.flush()

    rows = list_suppliers(keyword="同一采购供应商", status="all", db=db_session)

    assert len(rows) == 1
    assert rows[0]["orderCount"] == 1
