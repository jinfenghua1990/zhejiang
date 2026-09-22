"""套装档案批量删除：只删除没有历史业务引用的套装。"""

from datetime import datetime, timezone

from app.models.catalog import InventorySnapshot, ProductSku
from app.models.consumable import Consumable, ConsumableSkuMapping
from app.models.org import AuditLog


def test_bulk_delete_bundles_keeps_rows_with_history_references(client, db_session):
    removable = ProductSku(
        jackyun_sku_id="BULK-DELETE-REMOVABLE",
        sku_code="BULK-DELETE-REMOVABLE",
        sku_name="可删除套装",
        product_type="bundle",
    )
    referenced = ProductSku(
        jackyun_sku_id="BULK-DELETE-REFERENCED",
        sku_code="BULK-DELETE-REFERENCED",
        sku_name="有历史引用套装",
        product_type="virtual_bundle",
    )
    single = ProductSku(
        jackyun_sku_id="BULK-DELETE-SINGLE",
        sku_code="BULK-DELETE-SINGLE",
        sku_name="单品",
        product_type="single",
    )
    db_session.add_all([removable, referenced, single])
    db_session.flush()
    db_session.add(
        InventorySnapshot(
            sku_id=referenced.id,
            quantity=1,
            snapshot_at=datetime.now(timezone.utc),
            source="pytest",
        )
    )
    db_session.flush()
    removable_id, referenced_id, single_id = removable.id, referenced.id, single.id

    response = client.post(
        "/api/v1/dashboard/catalog/bundles/bulk-delete",
        json={"ids": [removable_id, referenced_id, single_id, 999999999]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] == 1
    assert body["deletedIds"] == [removable_id]
    assert body["blocked"][0]["id"] == referenced_id
    assert body["blocked"][0]["references"] == [{"label": "库存快照", "count": 1}]
    assert body["invalid"] == [{"id": single_id, "skuCode": "BULK-DELETE-SINGLE", "reason": "仅可删除套装或虚拟组合套装"}]
    assert body["notFound"] == [999999999]

    db_session.expire_all()
    assert db_session.get(ProductSku, removable_id) is None
    assert db_session.get(ProductSku, referenced_id) is not None

    db_session.query(InventorySnapshot).filter(InventorySnapshot.sku_id == referenced_id).delete(
        synchronize_session=False,
    )
    db_session.query(ProductSku).filter(ProductSku.id.in_([referenced_id, single_id])).delete(
        synchronize_session=False,
    )
    db_session.query(AuditLog).filter(AuditLog.action == "catalog.bundle.bulk_delete").delete(
        synchronize_session=False,
    )
    db_session.commit()


def test_bulk_delete_bundles_preview_does_not_delete(client, db_session):
    bundle = ProductSku(
        jackyun_sku_id="BULK-PREVIEW-001",
        sku_code="BULK-PREVIEW-001",
        sku_name="预览套装",
        product_type="bundle",
    )
    db_session.add(bundle)
    db_session.flush()
    bundle_id = bundle.id

    response = client.post(
        "/api/v1/dashboard/catalog/bundles/bulk-delete",
        json={"ids": [bundle_id], "dry_run": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dryRun"] is True
    assert body["deletedIds"] == [bundle_id]
    assert db_session.get(ProductSku, bundle_id) is not None

    db_session.delete(db_session.get(ProductSku, bundle_id))
    db_session.commit()


def test_bulk_delete_catalog_supports_single_and_consumable_rows(client, db_session):
    removable_goods = ProductSku(
        jackyun_sku_id="CATALOG-DELETE-GOODS-001",
        sku_code="CATALOG-DELETE-GOODS-001",
        sku_name="可删除单品",
        product_type="single",
    )
    referenced_goods = ProductSku(
        jackyun_sku_id="CATALOG-DELETE-GOODS-002",
        sku_code="CATALOG-DELETE-GOODS-002",
        sku_name="有库存引用单品",
        product_type="single",
    )
    bundle = ProductSku(
        jackyun_sku_id="CATALOG-DELETE-BUNDLE-001",
        sku_code="CATALOG-DELETE-BUNDLE-001",
        sku_name="套装不能从货品档案删除",
        product_type="bundle",
    )
    removable_consumable = Consumable(code="CATALOG-DELETE-MATERIAL-001", name="可删除耗材")
    referenced_consumable = Consumable(code="CATALOG-DELETE-MATERIAL-002", name="有映射耗材")
    db_session.add_all([removable_goods, referenced_goods, bundle, removable_consumable, referenced_consumable])
    db_session.flush()
    db_session.add(InventorySnapshot(sku_id=referenced_goods.id, quantity=1, snapshot_at=datetime.now(timezone.utc), source="pytest"))
    db_session.add(ConsumableSkuMapping(sku_id=referenced_goods.id, consumable_id=referenced_consumable.id))
    db_session.flush()

    response = client.post(
        "/api/v1/dashboard/catalog/bulk-delete",
        json={
            "items": [
                {"kind": "goods", "id": removable_goods.id},
                {"kind": "goods", "id": referenced_goods.id},
                {"kind": "goods", "id": bundle.id},
                {"kind": "consumable", "id": removable_consumable.id},
                {"kind": "consumable", "id": referenced_consumable.id},
                {"kind": "consumable", "id": 999999999},
            ],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] == 2
    assert body["deletedItems"] == [
        {"kind": "goods", "id": removable_goods.id, "code": "CATALOG-DELETE-GOODS-001"},
        {"kind": "consumable", "id": removable_consumable.id, "code": "CATALOG-DELETE-MATERIAL-001"},
    ]
    assert {item["id"] for item in body["blocked"]} == {referenced_goods.id, referenced_consumable.id}
    assert body["invalid"] == [{
        "kind": "goods",
        "id": bundle.id,
        "code": "CATALOG-DELETE-BUNDLE-001",
        "reason": "套装请在套装档案中操作",
    }]
    assert body["notFound"] == [{"kind": "consumable", "id": 999999999}]

    db_session.expire_all()
    assert db_session.get(ProductSku, removable_goods.id) is None
    assert db_session.get(Consumable, removable_consumable.id) is None
    assert db_session.get(ProductSku, referenced_goods.id) is not None
    assert db_session.get(Consumable, referenced_consumable.id) is not None

    db_session.query(InventorySnapshot).filter(InventorySnapshot.sku_id == referenced_goods.id).delete(synchronize_session=False)
    db_session.query(ConsumableSkuMapping).filter(ConsumableSkuMapping.consumable_id == referenced_consumable.id).delete(synchronize_session=False)
    db_session.query(ProductSku).filter(ProductSku.id.in_([referenced_goods.id, bundle.id])).delete(synchronize_session=False)
    db_session.query(Consumable).filter(Consumable.id == referenced_consumable.id).delete(synchronize_session=False)
    db_session.query(AuditLog).filter(AuditLog.action == "catalog.bulk_delete").delete(synchronize_session=False)
    db_session.commit()


def test_bulk_delete_catalog_blocked_reports_purchase_numbers(client, db_session):
    """被采购单占用时：references 携带真实单号（采购单号优先），reason 动态为「被采购单占用」。"""
    from app.models.alibaba1688_import import Alibaba1688Order
    from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
    from app.models.procurement_chain import ProcurementChainLink
    from app.models.purchase import PurchaseAllocationItem

    sku = ProductSku(
        jackyun_sku_id="CATALOG-DELETE-REF-NUM-001",
        sku_code="CATALOG-DELETE-REF-NUM-001",
        sku_name="被采购单占用单品",
        product_type="single",
    )
    document = JackyunGoodsDocument(
        document_type="inbound",
        goodsdoc_no="RK202504280001",
        document_at=datetime.now(timezone.utc),
    )
    db_session.add_all([sku, document])
    db_session.flush()
    document_item = JackyunGoodsDocumentItem(
        document_id=document.id,
        line_no=1,
        goods_no="CATALOG-DELETE-REF-NUM-001",
        matched_sku_id=sku.id,
    )
    db_session.add(document_item)
    db_session.flush()
    order = Alibaba1688Order(external_order_id="5125307904224001028", import_id=1)
    db_session.add(order)
    db_session.flush()
    db_session.add(ProcurementChainLink(
        order_id=order.id,
        target_type="inbound",
        target_id=document.id,
        match_method="auto",
    ))
    db_session.add(PurchaseAllocationItem(
        po_id=1,
        sku_id=sku.id,
        sku_code="CATALOG-DELETE-REF-NUM-001",
        source_item_id=document_item.id,
    ))
    db_session.flush()

    response = client.post(
        "/api/v1/dashboard/catalog/bulk-delete",
        json={"items": [{"kind": "goods", "id": sku.id}], "dry_run": True},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] == 0
    blocked = body["blocked"][0]
    assert blocked["reason"] == "该货品被采购单占用，不能删除"
    refs = {ref["label"]: ref for ref in blocked["references"]}
    assert refs["采购分摊"]["count"] == 1
    assert refs["采购分摊"]["numbers"] == ["5125307904224001028"]
    assert refs["吉客云单据匹配"]["count"] == 1
    assert refs["吉客云单据匹配"]["numbers"] == ["5125307904224001028"]
    # dry_run 不产生删除
    assert db_session.get(ProductSku, sku.id) is not None


def test_bulk_delete_catalog_blocked_falls_back_to_goodsdoc_no(client, db_session):
    """无有效采购链路（缺失/已拒绝）时回退为入库单号，reason 维持原文案。"""
    from app.models.alibaba1688_import import Alibaba1688Order
    from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
    from app.models.procurement_chain import ProcurementChainLink

    sku = ProductSku(
        jackyun_sku_id="CATALOG-DELETE-REF-NUM-002",
        sku_code="CATALOG-DELETE-REF-NUM-002",
        sku_name="仅入库单引用单品",
        product_type="single",
    )
    document = JackyunGoodsDocument(
        document_type="inbound",
        goodsdoc_no="RK202609160002",
        document_at=datetime.now(timezone.utc),
    )
    db_session.add_all([sku, document])
    db_session.flush()
    db_session.add(JackyunGoodsDocumentItem(
        document_id=document.id,
        line_no=1,
        matched_sku_id=sku.id,
    ))
    db_session.flush()
    order = Alibaba1688Order(external_order_id="5125307904224001029", import_id=1)
    db_session.add(order)
    db_session.flush()
    db_session.add(ProcurementChainLink(
        order_id=order.id,
        target_type="inbound",
        target_id=document.id,
        match_method="rejected",  # 已拒绝的链路不参与解析
    ))
    db_session.flush()

    response = client.post(
        "/api/v1/dashboard/catalog/bulk-delete",
        json={"items": [{"kind": "goods", "id": sku.id}], "dry_run": True},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    blocked = body["blocked"][0]
    assert blocked["reason"] == "已有历史业务引用，不能删除"
    refs = {ref["label"]: ref for ref in blocked["references"]}
    assert refs["吉客云单据匹配"]["numbers"] == ["RK202609160002"]


def test_bulk_delete_catalog_blocked_reports_sales_order_numbers(client, db_session):
    """被销售订单占用时：references 携带真实销售单号，reason 动态为「被销售订单占用」。"""
    from app.models.sales import SalesOrder, SalesOrderItem

    sku = ProductSku(
        jackyun_sku_id="CATALOG-DELETE-REF-NUM-003",
        sku_code="CATALOG-DELETE-REF-NUM-003",
        sku_name="被销售订单占用单品",
        product_type="single",
    )
    order = SalesOrder(order_no="SO202609160003")
    db_session.add_all([sku, order])
    db_session.flush()
    db_session.add(SalesOrderItem(
        order_id=order.id,
        sku_id=sku.id,
        sku_code="CATALOG-DELETE-REF-NUM-003",
        quantity=1,
    ))
    db_session.flush()
    sku_id, order_id = sku.id, order.id

    response = client.post(
        "/api/v1/dashboard/catalog/bulk-delete",
        json={"items": [{"kind": "goods", "id": sku_id}], "dry_run": True},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] == 0
    blocked = body["blocked"][0]
    assert blocked["reason"] == "该货品被销售订单占用，不能删除"
    refs = {ref["label"]: ref for ref in blocked["references"]}
    assert refs["销售订单"]["count"] == 1
    assert refs["销售订单"]["numbers"] == ["SO202609160003"]
    # dry_run 不产生删除
    assert db_session.get(ProductSku, sku_id) is not None

    db_session.query(SalesOrderItem).filter(SalesOrderItem.order_id == order_id).delete(
        synchronize_session=False,
    )
    db_session.query(SalesOrder).filter(SalesOrder.id == order_id).delete(synchronize_session=False)
    db_session.query(ProductSku).filter(ProductSku.id == sku_id).delete(synchronize_session=False)
    db_session.query(AuditLog).filter(AuditLog.action == "catalog.bulk_delete").delete(
        synchronize_session=False,
    )
    db_session.commit()


def test_bulk_delete_catalog_blocked_matches_sales_order_by_sku_code(client, db_session):
    """销售明细 sku_id 为空、仅带 sku_code 时也应 blocked 且产出真实销售单号；大小写不一致的编码同样命中。"""
    from app.models.sales import SalesOrder, SalesOrderItem

    exact = ProductSku(
        jackyun_sku_id="CATALOG-DELETE-REF-NUM-004",
        sku_code="CATALOG-DELETE-REF-NUM-004",
        sku_name="编码精确匹配单品",
        product_type="single",
    )
    mixed_case = ProductSku(
        jackyun_sku_id="CATALOG-DELETE-REF-NUM-005",
        sku_code="CATALOG-DELETE-REF-NUM-005",
        sku_name="编码大小写不一致单品",
        product_type="single",
    )
    order_exact = SalesOrder(order_no="SO202609160004")
    order_mixed = SalesOrder(order_no="SO202609160005")
    db_session.add_all([exact, mixed_case, order_exact, order_mixed])
    db_session.flush()
    db_session.add_all([
        SalesOrderItem(
            order_id=order_exact.id,
            sku_id=None,
            sku_code="CATALOG-DELETE-REF-NUM-004",
            quantity=1,
        ),
        SalesOrderItem(
            order_id=order_mixed.id,
            sku_id=None,
            sku_code="catalog-delete-ref-num-005",
            quantity=2,
        ),
    ])
    db_session.flush()
    exact_id, mixed_id = exact.id, mixed_case.id
    order_ids = [order_exact.id, order_mixed.id]

    response = client.post(
        "/api/v1/dashboard/catalog/bulk-delete",
        json={"items": [{"kind": "goods", "id": exact_id}, {"kind": "goods", "id": mixed_id}], "dry_run": True},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] == 0
    blocked_by_id = {item["id"]: item for item in body["blocked"]}
    assert set(blocked_by_id) == {exact_id, mixed_id}
    for item in blocked_by_id.values():
        assert item["reason"] == "该货品被销售订单占用，不能删除"
    refs_exact = {ref["label"]: ref for ref in blocked_by_id[exact_id]["references"]}
    refs_mixed = {ref["label"]: ref for ref in blocked_by_id[mixed_id]["references"]}
    assert refs_exact["销售订单"]["count"] == 1
    assert refs_exact["销售订单"]["numbers"] == ["SO202609160004"]
    assert refs_mixed["销售订单"]["count"] == 1
    assert refs_mixed["销售订单"]["numbers"] == ["SO202609160005"]
    # dry_run 不产生删除
    assert db_session.get(ProductSku, exact_id) is not None
    assert db_session.get(ProductSku, mixed_id) is not None

    db_session.query(SalesOrderItem).filter(SalesOrderItem.order_id.in_(order_ids)).delete(
        synchronize_session=False,
    )
    db_session.query(SalesOrder).filter(SalesOrder.id.in_(order_ids)).delete(synchronize_session=False)
    db_session.query(ProductSku).filter(ProductSku.id.in_([exact_id, mixed_id])).delete(
        synchronize_session=False,
    )
    db_session.commit()


def test_bulk_status_updates_goods_and_consumables(client, db_session):
    """批量停用/启用：goods 与 consumable 均可更新，套装不限，找不到记 notFound。"""
    goods = ProductSku(
        jackyun_sku_id="CATALOG-STATUS-GOODS-001",
        sku_code="CATALOG-STATUS-GOODS-001",
        sku_name="停用单品",
        product_type="bundle",  # 停用不限类型，套装也允许
    )
    consumable = Consumable(code="CATALOG-STATUS-MATERIAL-001", name="停用耗材")
    db_session.add_all([goods, consumable])
    db_session.flush()
    goods_id, consumable_id = goods.id, consumable.id

    response = client.post(
        "/api/v1/dashboard/catalog/bulk-status",
        json={
            "items": [
                {"kind": "goods", "id": goods_id},
                {"kind": "consumable", "id": consumable_id},
                {"kind": "goods", "id": 999999999},
            ],
            "status": "inactive",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["updated"] == 2
    assert body["items"] == [
        {"kind": "goods", "id": goods_id, "code": "CATALOG-STATUS-GOODS-001", "status": "inactive"},
        {"kind": "consumable", "id": consumable_id, "code": "CATALOG-STATUS-MATERIAL-001", "status": "inactive"},
    ]
    assert body["notFound"] == [{"kind": "goods", "id": 999999999}]

    db_session.expire_all()
    assert db_session.get(ProductSku, goods_id).status == "inactive"
    assert db_session.get(Consumable, consumable_id).status == "inactive"

    restore = client.post(
        "/api/v1/dashboard/catalog/bulk-status",
        json={"items": [{"kind": "goods", "id": goods_id}, {"kind": "consumable", "id": consumable_id}], "status": "active"},
    )
    assert restore.status_code == 200
    assert restore.json()["updated"] == 2
    db_session.expire_all()
    assert db_session.get(ProductSku, goods_id).status == "active"

    db_session.query(ProductSku).filter(ProductSku.id == goods_id).delete(synchronize_session=False)
    db_session.query(Consumable).filter(Consumable.id == consumable_id).delete(synchronize_session=False)
    db_session.query(AuditLog).filter(AuditLog.action == "catalog.bulk_status").delete(synchronize_session=False)
    db_session.commit()


def test_bulk_status_rejects_invalid_status(client):
    response = client.post(
        "/api/v1/dashboard/catalog/bulk-status",
        json={"items": [{"kind": "goods", "id": 1}], "status": "disabled"},
    )
    assert response.status_code == 400
