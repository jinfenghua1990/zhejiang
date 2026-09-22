"""正品库存独立运算：采购入库 − 销售出库 = 库存。

不依赖吉客云库存快照，直接对本地导入的出入库单据全量汇总；仓库归属用本系统
仓库档案（jackyun_warehouse_id/编码/名称三重匹配），映射不到的单据量进入
未映射仓库桶，不会混入任何已映射仓库。这个服务只读本地副本，不触发外部查询。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.catalog import Product, ProductSku, Warehouse
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.models.inventory_stocktake import InventoryStocktakeItem, InventoryStocktakeTask
from app.utils.money import to_decimal


def _key(value: object) -> str:
    return str(value or "").strip().lower()


def _sku_lookup(db: Session) -> tuple[dict[str, int], dict[int, ProductSku]]:
    """返回只保留唯一命中的货品号/SKU/条码索引，避免错关联。"""
    skus = db.query(ProductSku).filter(ProductSku.status == "active").all()
    products = {row.id: row for row in db.query(Product).all()}
    candidates: dict[str, set[int]] = defaultdict(set)
    by_id = {row.id: row for row in skus}
    for sku in skus:
        for value in (sku.jackyun_sku_id, sku.sku_code, sku.barcode):
            if _key(value):
                candidates[_key(value)].add(sku.id)

    product_sku_ids: dict[int, list[int]] = defaultdict(list)
    for sku in skus:
        if sku.product_id is not None:
            product_sku_ids[sku.product_id].append(sku.id)
    for product in products.values():
        ids = product_sku_ids.get(product.id, [])
        if len(ids) != 1:
            continue
        for value in (product.jackyun_goods_id, product.goods_code):
            if _key(value):
                candidates[_key(value)].add(ids[0])

    return {
        key: next(iter(ids))
        for key, ids in candidates.items()
        if len(ids) == 1
    }, by_id


def _warehouse_lookup(db: Session) -> dict[str, int]:
    lookup: dict[str, int] = {}
    for row in db.query(Warehouse).all():
        values = (row.jackyun_warehouse_id, row.code, row.name)
        for value in values:
            key = _key(value)
            if key:
                lookup.setdefault(key, row.id)
    return lookup


def _resolve_sku_id(
    item: JackyunGoodsDocumentItem,
    lookup: dict[str, int],
    by_id: dict[int, ProductSku],
) -> int | None:
    if item.matched_sku_id is not None and item.matched_sku_id in by_id:
        return int(item.matched_sku_id)
    for value in (
        item.sku_barcode,
        item.goods_no,
        (item.raw or {}).get("skuBarcode"),
        (item.raw or {}).get("goodsNo"),
        (item.raw or {}).get("goodsCode"),
    ):
        resolved = lookup.get(_key(value))
        if resolved is not None:
            return resolved
    return None


def current_positions(db: Session) -> dict[str, Any]:
    """独立运算当前正品库存：Σ采购入库 − Σ销售出库，仓库按本系统仓库档案归属。

    ``by_sku`` 与 ``by_sku_warehouse`` 的数值是可用于业务计算的当前数量；
    ``seen_skus`` 记录至少出现在一张单据中的 SKU。无法唯一匹配的出入库明细
    不会被猜测写入库存，只在 diagnostics 中暴露。
    """
    lookup, sku_by_id = _sku_lookup(db)
    warehouse_lookup = _warehouse_lookup(db)
    by_sku_warehouse: dict[int, dict[int | None, Decimal]] = defaultdict(dict)
    by_sku: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    seen_skus: set[int] = set()

    applied_documents = 0
    applied_items = 0
    unmatched_items = 0
    applied_inbound = Decimal("0")
    applied_outbound = Decimal("0")
    first_at: datetime | None = None
    last_at: datetime | None = None

    rows = (
        db.query(JackyunGoodsDocumentItem, JackyunGoodsDocument)
        .join(JackyunGoodsDocument, JackyunGoodsDocument.id == JackyunGoodsDocumentItem.document_id)
        .filter(JackyunGoodsDocument.document_type.in_(("inbound", "outbound")))
        .order_by(JackyunGoodsDocument.document_at, JackyunGoodsDocument.id)
        .all()
    )
    seen_documents: set[int] = set()
    for item, document in rows:
        if document.document_at is not None:
            if first_at is None or document.document_at < first_at:
                first_at = document.document_at
            if last_at is None or document.document_at > last_at:
                last_at = document.document_at
        warehouse_id = warehouse_lookup.get(_key(document.warehouse_code))
        if warehouse_id is None:
            warehouse_id = warehouse_lookup.get(_key(document.warehouse_name))
        sign = Decimal("1") if document.document_type == "inbound" else Decimal("-1")
        sku_id = _resolve_sku_id(item, lookup, sku_by_id)
        quantity = to_decimal(item.quantity)
        if sku_id is None or quantity <= 0:
            if quantity > 0:
                unmatched_items += 1
            continue
        bucket = by_sku_warehouse[sku_id]
        bucket[warehouse_id] = bucket.get(warehouse_id, Decimal("0")) + sign * quantity
        by_sku[sku_id] += sign * quantity
        seen_skus.add(sku_id)
        applied_items += 1
        seen_documents.add(document.id)
        if document.document_type == "inbound":
            applied_inbound += quantity
        else:
            applied_outbound += quantity
    applied_documents = len(seen_documents)

    # 已完成盘点的正品差异是本地库存调整事实；只在确认后进入运算库存。
    stocktake_rows = (
        db.query(InventoryStocktakeItem, InventoryStocktakeTask)
        .join(InventoryStocktakeTask, InventoryStocktakeTask.id == InventoryStocktakeItem.task_id)
        .filter(
            InventoryStocktakeTask.status == "completed",
            InventoryStocktakeItem.item_kind == "goods",
            InventoryStocktakeItem.difference_qty.is_not(None),
        )
        .order_by(InventoryStocktakeTask.confirmed_at, InventoryStocktakeTask.id, InventoryStocktakeItem.id)
        .all()
    )
    stocktake_adjustment_count = 0
    for item, task in stocktake_rows:
        diff = to_decimal(item.difference_qty)
        if diff == 0:
            continue
        bucket = by_sku_warehouse[item.ref_id]
        bucket[item.warehouse_id] = bucket.get(item.warehouse_id, Decimal("0")) + diff
        by_sku[item.ref_id] += diff
        seen_skus.add(item.ref_id)
        stocktake_adjustment_count += 1
        if task.confirmed_at is not None and (last_at is None or task.confirmed_at > last_at):
            last_at = task.confirmed_at

    return {
        "by_sku": dict(by_sku),
        "by_sku_warehouse": {sku_id: dict(values) for sku_id, values in by_sku_warehouse.items()},
        "seen_skus": seen_skus,
        "first_document_at": first_at,
        "last_document_at": last_at,
        "applied_document_count": applied_documents,
        "applied_item_count": applied_items,
        "unmatched_item_count": unmatched_items,
        "stocktake_adjustment_count": stocktake_adjustment_count,
        "applied_inbound_quantity": applied_inbound,
        "applied_outbound_quantity": applied_outbound,
        "source": "documents_plus_stocktakes",
    }


def sku_id_for_document_item(db: Session, item: JackyunGoodsDocumentItem) -> int | None:
    """供周转统计复用的安全 SKU 解析。"""
    lookup, sku_by_id = _sku_lookup(db)
    return _resolve_sku_id(item, lookup, sku_by_id)


def sku_transactions(db: Session, sku_id: int, limit: int = 500) -> dict[str, Any]:
    """正品（SKU）逐笔出入库流水：来自本地吉客云入库/出库单明细，真实单据事实。

    SKU 解析口径与 current_positions 完全一致（matched_sku_id 优先，条码/货品号兜底），
    保证流水合计 = 库存总览的运算库存。结存按时间正序累计后倒序返回。
    """
    sku = db.get(ProductSku, sku_id)
    if sku is None:
        raise ValueError("SKU 不存在")
    lookup, sku_by_id = _sku_lookup(db)
    warehouse_lookup = _warehouse_lookup(db)
    product = db.get(Product, sku.product_id) if sku.product_id is not None else None

    movements: list[dict[str, Any]] = []
    rows = (
        db.query(JackyunGoodsDocumentItem, JackyunGoodsDocument)
        .join(JackyunGoodsDocument, JackyunGoodsDocument.id == JackyunGoodsDocumentItem.document_id)
        .filter(JackyunGoodsDocument.document_type.in_(("inbound", "outbound")))
        .order_by(JackyunGoodsDocument.document_at.nulls_last(), JackyunGoodsDocument.id, JackyunGoodsDocumentItem.line_no)
        .all()
    )
    balance = Decimal("0")
    for item, document in rows:
        if _resolve_sku_id(item, lookup, sku_by_id) != sku_id:
            continue
        quantity = to_decimal(item.quantity)
        if quantity is None or quantity <= 0:
            continue
        sign = Decimal("1") if document.document_type == "inbound" else Decimal("-1")
        before = balance
        balance += sign * quantity
        warehouse_id = warehouse_lookup.get(_key(document.warehouse_code))
        if warehouse_id is None:
            warehouse_id = warehouse_lookup.get(_key(document.warehouse_name))
        movements.append({
            "occurredAt": document.document_at.isoformat() if document.document_at else None,
            "direction": document.document_type,
            "documentNo": document.goodsdoc_no,
            "warehouseId": warehouse_id,
            "warehouseName": document.warehouse_name or "未映射仓库",
            "quantity": float(sign * quantity),
            "balanceBefore": float(before),
            "balanceAfter": float(balance),
            "supplierName": document.supplier_name or "",
            "companyName": document.company_name or "",
            "matchedSkuId": item.matched_sku_id,
            "matchStatus": item.match_status or "",
        })

    stocktake_rows = (
        db.query(InventoryStocktakeItem, InventoryStocktakeTask, Warehouse)
        .join(InventoryStocktakeTask, InventoryStocktakeTask.id == InventoryStocktakeItem.task_id)
        .join(Warehouse, Warehouse.id == InventoryStocktakeItem.warehouse_id)
        .filter(
            InventoryStocktakeTask.status == "completed",
            InventoryStocktakeItem.item_kind == "goods",
            InventoryStocktakeItem.ref_id == sku_id,
            InventoryStocktakeItem.difference_qty.is_not(None),
        )
        .all()
    )
    for item, task, warehouse in stocktake_rows:
        diff = to_decimal(item.difference_qty)
        if diff == 0:
            continue
        movements.append({
            "occurredAt": task.confirmed_at.isoformat() if task.confirmed_at else None,
            "direction": "stocktake",
            "documentNo": task.number,
            "warehouseId": warehouse.id,
            "warehouseName": warehouse.name,
            "quantity": float(diff),
            "balanceBefore": 0.0,
            "balanceAfter": 0.0,
            "supplierName": "",
            "companyName": "",
            "matchedSkuId": sku_id,
            "matchStatus": "stocktake",
        })

    movements.sort(key=lambda row: (row.get("occurredAt") or "", row.get("documentNo") or ""))
    balance = Decimal("0")
    for movement in movements:
        before = balance
        balance += to_decimal(movement["quantity"])
        movement["balanceBefore"] = float(before)
        movement["balanceAfter"] = float(balance)

    movements.reverse()
    return {
        "sku": {
            "skuId": sku.id,
            "skuCode": sku.sku_code,
            "skuName": sku.sku_name,
            "barcode": sku.barcode,
            "unit": sku.unit,
            "goodsName": product.goods_name if product else "",
        },
        "total": len(movements),
        "rows": movements[: max(1, limit)],
        "balance": float(balance),
    }
