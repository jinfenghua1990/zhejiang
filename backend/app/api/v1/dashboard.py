from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import dashboard
from app.api.deps import current_actor, require_roles
from app.core.audit import audit
from app.models.alibaba1688_import import Alibaba1688Order
from app.models.catalog import Product, ProductSku
from app.models.catalog import InventorySnapshot
from app.models.consumable import Consumable, ConsumableSkuMapping, ConsumableTransaction, InboundConsumableUsage
from app.models.consumable_purchase import ConsumablePurchaseItem
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.models.org import User
from app.models.production import (
    ProductionFinishedMovement,
    ProductionInboundAllocation,
    ProductionMaterialMovement,
    ProductionMaterialReservation,
    ProductionOrderItem,
)
from app.models.profit import CostSnapshot
from app.models.procurement_chain import ProcurementChainLink
from app.models.purchase import ExternalPurchaseOrder, PurchaseAllocationItem
from app.models.sales import SalesOrder, SalesOrderItem
from app.models.tax import TaxAccountingCategoryRule
from app.services import tax_category_rule_service
from app.utils.money import to_decimal

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/sales-trend")
def sales_trend(
    days: int = 30,
    start: date | None = Query(None, description="区间起（含），传了优先于 days"),
    end: date | None = Query(None, description="区间止（含当天）"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return dashboard.sales_trend(db, days=min(max(days, 7), 365), start=start, end=end)


@router.get("/platform-ranking")
def platform_ranking(
    start: date | None = Query(None),
    end: date | None = Query(None),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return dashboard.platform_ranking(db, start=start, end=end)


@router.get("/sku-ranking")
def sku_ranking(
    limit: int = 20,
    start: date | None = Query(None),
    end: date | None = Query(None),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return dashboard.sku_ranking(db, limit=min(max(limit, 1), 100), start=start, end=end)


@router.get("/inventory")
def inventory(db: Session = Depends(get_db)) -> dict[str, Any]:
    return dashboard.inventory_summary(db)


@router.get("/inventory/skus")
def inventory_skus(search: str = "", limit: int = 1000,
                   db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return dashboard.inventory_skus(db, search=search, limit=min(max(limit, 1), 2000))


@router.get("/inventory/sku-transactions")
def inventory_sku_transactions(sku_id: int, limit: int = 500,
                               db: Session = Depends(get_db)) -> dict[str, Any]:
    """正品 SKU 逐笔出入库流水（本地吉客云单据事实，口径与库存运算一致）。"""
    from app.services.inventory_position_service import sku_transactions
    try:
        return sku_transactions(db, sku_id, limit=min(max(limit, 1), 2000))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/products")
def products(search: str = "", limit: int = 200,
             db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return dashboard.list_products(db, search=search, limit=min(max(limit, 1), 500))


@router.get("/catalog-unified")
def catalog_unified(kind: str = "all", search: str = "", limit: int = 500,
                    db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """统一货品档案：正品（吉客云）+ 耗材（本平台）合列表；kind=all/goods/consumable。"""
    if kind not in {"all", "goods", "consumable"}:
        kind = "all"
    return dashboard.catalog_unified(db, kind=kind, search=search, limit=min(max(limit, 1), 2000))


class ProductSkuBody(BaseModel):
    sku_id: int | None = None
    jackyun_sku_id: str | None = None
    sku_code: str
    product_type: str | None = None
    sku_name: str = ""
    barcode: str = ""
    unit: str = ""
    sale_price: str | None = None
    default_cost: str | None = None
    cost_mode: str = "fixed"
    cost_tolerance_pct: str = "0.0200"
    tax_code: str = ""
    tax_category_rule_id: int | None = None
    goods_category: str = ""
    status: str = "active"


@router.post("/products/save")
def save_product(body: ProductSkuBody, request: Request,
                 _editor: User = Depends(require_roles("admin", "operator")),
                 db: Session = Depends(get_db)) -> dict[str, Any]:
    code = body.sku_code.strip()
    sku = db.get(ProductSku, body.sku_id) if body.sku_id else None
    external_id = (body.jackyun_sku_id or (sku.jackyun_sku_id if sku else code)).strip()
    if not code or not external_id:
        raise HTTPException(400, "SKU 编码不能为空")
    if body.cost_mode not in {"fixed", "dynamic"}:
        raise HTTPException(400, "成本方式必须是 fixed 或 dynamic")
    product_type = body.product_type or ("virtual_bundle" if code.upper().startswith("ES") else "single")
    if product_type not in {"single", "bundle", "virtual_bundle"}:
        raise HTTPException(400, "货品类型必须是 single 或 bundle")
    try:
        sale_price = to_decimal(body.sale_price) if body.sale_price not in (None, "") else None
        default_cost = to_decimal(body.default_cost) if body.default_cost not in (None, "") else None
        tolerance = to_decimal(body.cost_tolerance_pct)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "金额或成本容差格式不正确") from exc
    if any(value is not None and value < 0 for value in (sale_price, default_cost)):
        raise HTTPException(400, "售价和成本不能为负数")
    if tolerance < 0 or tolerance > 1:
        raise HTTPException(400, "成本容差必须在 0 到 1 之间")
    tax_rule = None
    if body.tax_category_rule_id is not None:
        tax_rule = db.get(TaxAccountingCategoryRule, body.tax_category_rule_id)
        if tax_rule is None:
            raise HTTPException(404, "财务分类规则不存在")
        tax_code = tax_rule.tax_code or ""
    else:
        try:
            tax_code = tax_category_rule_service.normalize_tax_code(body.tax_code)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        tax_rule = tax_category_rule_service.unique_enabled_rule_for_tax_code(db, tax_code)
    code_conflict = db.query(ProductSku).filter(func.lower(ProductSku.sku_code) == code.lower())
    if sku:
        code_conflict = code_conflict.filter(ProductSku.id != sku.id)
    if code_conflict.first():
        raise HTTPException(409, "SKU 编码已存在")
    conflict = db.query(ProductSku).filter(ProductSku.jackyun_sku_id == external_id)
    if sku:
        conflict = conflict.filter(ProductSku.id != sku.id)
    if conflict.first():
        raise HTTPException(409, "吉客云 SKU ID 已存在")
    if sku is None:
        sku = ProductSku(jackyun_sku_id=external_id, sku_code=code)
        db.add(sku)
    sku.jackyun_sku_id = external_id
    sku.sku_code = code
    sku.product_type = product_type
    sku.sku_name = body.sku_name.strip()
    sku.barcode = body.barcode.strip()
    sku.unit = body.unit.strip()
    sku.sale_price = sale_price
    sku.default_cost = default_cost
    sku.cost_mode = body.cost_mode
    sku.cost_tolerance_pct = tolerance
    sku.tax_code = tax_code
    sku.tax_category_rule_id = tax_rule.id if tax_rule is not None else None
    sku.status = body.status.strip() or "active"
    sku.raw = {**(sku.raw or {}), "managedLocally": True}
    # 品类（如 咖啡豆/饼干）存在货品主档 Product.category；仅当本地档案已修改时回写
    if sku.product_id:
        product = db.get(Product, sku.product_id)
        if product is not None and body.goods_category.strip() != (product.category or ""):
            product.category = body.goods_category.strip()
            product.raw = {**(product.raw or {}), "categoryLocallyEdited": True}
    db.commit()
    audit(db, current_actor(request), "catalog.product_sku.save", "product_skus", sku.id, {"skuCode": sku.sku_code})
    return {"id": sku.id, "skuCode": sku.sku_code, "jackyunSkuId": sku.jackyun_sku_id}


class CostPolicyBody(BaseModel):
    cost_mode: str
    cost_tolerance_pct: str | None = None


@router.post("/products/{sku_id}/cost-policy")
def update_cost_policy(sku_id: int, body: CostPolicyBody, request: Request,
                       _editor: User = Depends(require_roles("admin", "operator")),
                       db: Session = Depends(get_db)) -> dict[str, str | int]:
    if body.cost_mode not in {"fixed", "dynamic"}:
        raise HTTPException(400, "成本方式必须是 fixed 或 dynamic")
    sku = db.get(ProductSku, sku_id)
    if sku is None:
        raise HTTPException(404, "SKU 不存在")
    if body.cost_tolerance_pct is not None:
        try:
            tolerance = Decimal(body.cost_tolerance_pct)
        except Exception as exc:
            raise HTTPException(400, "成本容差格式不正确") from exc
        if tolerance < 0 or tolerance > 1:
            raise HTTPException(400, "成本容差必须在 0 到 1 之间")
        sku.cost_tolerance_pct = tolerance
    sku.cost_mode = body.cost_mode
    db.commit()
    audit(db, current_actor(request), "catalog.cost_policy.update", "product_skus", sku.id, {"mode": sku.cost_mode})
    return {"id": sku.id, "costMode": sku.cost_mode, "costTolerancePct": str(sku.cost_tolerance_pct)}


class ConsumablePolicyBody(BaseModel):
    policy: Literal["auto", "none"]


@router.post("/products/{sku_id}/consumable-policy")
def update_consumable_policy(sku_id: int, body: ConsumablePolicyBody, request: Request,
                             _editor: User = Depends(require_roles("admin", "operator")),
                             db: Session = Depends(get_db)) -> dict[str, str | int]:
    """保存货品是否需要绑定耗材的主档规则，不改变已有库存流水。"""
    sku = db.get(ProductSku, sku_id)
    if sku is None:
        raise HTTPException(404, "SKU 不存在")
    sku.raw = {**(sku.raw or {}), "consumablePolicy": body.policy}
    db.commit()
    audit(db, current_actor(request), "catalog.consumable_policy.update", "product_skus", sku.id, {"policy": body.policy})
    return {"id": sku.id, "consumablePolicy": body.policy}


class CategoryBody(BaseModel):
    kind: str  # goods / consumable
    id: int
    category: str


@router.post("/catalog/category")
def update_category(body: CategoryBody, request: Request,
                    _editor: User = Depends(require_roles("admin", "operator")),
                    db: Session = Depends(get_db)) -> dict[str, Any]:
    """行内修改品类：正品写 Product.category（吉客云同步后以本地值为准），耗材写 Consumable.category。"""
    category = body.category.strip()
    if len(category) > 128:
        raise HTTPException(400, "品类名称过长")
    if body.kind == "goods":
        sku = db.get(ProductSku, body.id)
        if sku is None:
            raise HTTPException(404, "SKU 不存在")
        if sku.product_id is None:
            raise HTTPException(400, "该 SKU 未关联货品主档，请先在编辑表单中补充")
        product = db.get(Product, sku.product_id)
        if product is None:
            raise HTTPException(404, "货品主档不存在")
        before = product.category or ""
        product.category = category
        product.raw = {**(product.raw or {}), "categoryLocallyEdited": True}
        target, label = "products", sku.sku_code
    elif body.kind == "consumable":
        row = db.get(Consumable, body.id)
        if row is None:
            raise HTTPException(404, "耗材不存在")
        before = row.category or ""
        row.category = category
        target, label = "consumables", row.code
    else:
        raise HTTPException(400, "kind 必须是 goods 或 consumable")
    db.commit()
    audit(db, current_actor(request), "catalog.category.update", target, body.id,
          {"code": label, "before": before, "after": category})
    return {"ok": True, "kind": body.kind, "id": body.id, "category": category}


class TaxCodeBulkBody(BaseModel):
    items: list[dict[str, Any]]  # [{"kind": "goods"|"consumable", "id": 1}, ...]
    tax_code: str
    overwrite: bool = False      # False=仅填空缺；True=覆盖已有值


@router.post("/catalog/tax-code/bulk")
def bulk_set_tax_code(body: TaxCodeBulkBody, request: Request,
                      _editor: User = Depends(require_roles("admin", "operator")),
                      db: Session = Depends(get_db)) -> dict[str, Any]:
    """批量设置货品档案（正品 SKU / 耗材）的税收分类编码。"""
    try:
        tax_code = tax_category_rule_service.normalize_tax_code(body.tax_code)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not tax_code:
        raise HTTPException(400, "税务代码不能为空")
    if not body.items:
        raise HTTPException(400, "请先勾选要设置的货品")
    updated, skipped, missing = 0, 0, 0
    for item in body.items:
        kind, row_id = item.get("kind"), item.get("id")
        model = ProductSku if kind == "goods" else Consumable if kind == "consumable" else None
        if model is None or not isinstance(row_id, int):
            missing += 1
            continue
        row = db.get(model, row_id)
        if row is None:
            missing += 1
            continue
        if not body.overwrite and (row.tax_code or "").strip():
            skipped += 1
            continue
        row.tax_code = tax_code
        matching_rule = tax_category_rule_service.unique_enabled_rule_for_tax_code(db, tax_code)
        row.tax_category_rule_id = matching_rule.id if matching_rule is not None else None
        updated += 1
    db.commit()
    audit(db, current_actor(request), "catalog.tax_code.bulk", "catalog", None,
          {"taxCode": tax_code, "updated": updated, "skipped": skipped, "missing": missing})
    return {"updated": updated, "skipped": skipped, "missing": missing}


class CatalogBulkDeleteItem(BaseModel):
    kind: Literal["goods", "consumable"]
    id: int


class CatalogBulkDeleteBody(BaseModel):
    items: list[CatalogBulkDeleteItem]
    dry_run: bool = False


_PRODUCT_SKU_REFERENCE_SOURCES = (
    (InventorySnapshot.sku_id, "库存快照"),
    (ConsumableSkuMapping.sku_id, "耗材映射"),
    (PurchaseAllocationItem.sku_id, "采购分摊"),
    (SalesOrderItem.sku_id, "销售订单"),
    (ProductionOrderItem.sku_id, "生产订单"),
    (ProductionFinishedMovement.sku_id, "生产完成"),
    (ProductionInboundAllocation.sku_id, "生产入库关联"),
    (JackyunGoodsDocumentItem.matched_sku_id, "吉客云单据匹配"),
    (CostSnapshot.sku_id, "成本快照"),
)


_CONSUMABLE_REFERENCE_SOURCES = (
    (ConsumableSkuMapping.consumable_id, "耗材映射"),
    (ConsumableTransaction.consumable_id, "耗材库存流水"),
    (InboundConsumableUsage.consumable_id, "入库耗材使用"),
    (ConsumablePurchaseItem.consumable_id, "耗材采购明细"),
    (ProductionMaterialReservation.consumable_id, "生产耗材预留"),
    (ProductionMaterialMovement.consumable_id, "生产耗材流转"),
)


def _catalog_reference_counts(db: Session, sources: tuple[tuple[Any, str], ...], ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    references: dict[int, list[dict[str, Any]]] = {row_id: [] for row_id in ids}
    for column, label in sources:
        if label == "销售订单":
            # 销售明细多只带 sku_code 文本（sku_id 列常为空）：sku_id OR sku_code 双通道匹配（大小写不敏感），
            # 按货品分组计数避免同一明细同时命中两通道时重复计数。
            counts = (
                db.query(ProductSku.id, func.count(SalesOrderItem.id))
                .join(
                    SalesOrderItem,
                    or_(
                        SalesOrderItem.sku_id == ProductSku.id,
                        and_(
                            SalesOrderItem.sku_code != "",
                            ProductSku.sku_code != "",
                            func.lower(SalesOrderItem.sku_code) == func.lower(ProductSku.sku_code),
                        ),
                    ),
                )
                .filter(ProductSku.id.in_(ids))
                .group_by(ProductSku.id)
                .all()
            ) if ids else []
            for row_id, count in counts:
                if int(count) > 0:
                    references[int(row_id)].append({"label": label, "count": int(count)})
            continue
        counts = (
            db.query(column, func.count())
            .filter(column.in_(ids))
            .group_by(column)
            .all()
        ) if ids else []
        for row_id, count in counts:
            if row_id is not None and int(count) > 0:
                references[int(row_id)].append({"label": label, "count": int(count)})
    return references


def _inbound_reference_numbers(db: Session, item_ids: list[int]) -> dict[int, str]:
    """按入库单明细行解析占用单号：优先关联的采购单号（1688/外部），否则入库单号。"""
    numbers: dict[int, str] = {}
    if not item_ids:
        return numbers
    item_rows = (
        db.query(JackyunGoodsDocumentItem.id, JackyunGoodsDocumentItem.document_id, JackyunGoodsDocument.goodsdoc_no)
        .join(JackyunGoodsDocument, JackyunGoodsDocument.id == JackyunGoodsDocumentItem.document_id)
        .filter(JackyunGoodsDocumentItem.id.in_(item_ids))
        .all()
    )
    document_ids = {document_id for _, document_id, _ in item_rows}
    po_numbers: dict[int, str] = {}
    if document_ids:
        links = (
            db.query(ProcurementChainLink)
            .filter(
                ProcurementChainLink.target_type == "inbound",
                ProcurementChainLink.target_id.in_(document_ids),
                ProcurementChainLink.match_method != "rejected",
            )
            .order_by(ProcurementChainLink.id)
            .all()
        )
        alibaba_ids = {link.order_id for link in links if link.order_id is not None}
        external_ids = {link.external_po_id for link in links if link.external_po_id is not None}
        alibaba_orders = (
            dict(
                db.query(Alibaba1688Order.id, Alibaba1688Order.external_order_id)
                .filter(Alibaba1688Order.id.in_(alibaba_ids))
                .all()
            )
            if alibaba_ids
            else {}
        )
        external_orders = (
            dict(
                db.query(ExternalPurchaseOrder.id, ExternalPurchaseOrder.external_order_id)
                .filter(ExternalPurchaseOrder.id.in_(external_ids))
                .all()
            )
            if external_ids
            else {}
        )
        for link in links:
            if link.target_id in po_numbers:
                continue
            number = ""
            if link.order_id is not None:
                number = alibaba_orders.get(link.order_id, "")
            elif link.external_po_id is not None:
                number = external_orders.get(link.external_po_id, "")
            if number:
                po_numbers[link.target_id] = number
    for item_id, document_id, goodsdoc_no in item_rows:
        numbers[item_id] = po_numbers.get(document_id) or goodsdoc_no
    return numbers


def _reference_numbers(db: Session, source: tuple[Any, str], row_ids: list[int]) -> dict[int, list[str]]:
    """解析引用来源占用的真实单号；仅采购分摊 / 吉客云单据匹配 / 销售订单三类可解析，其余返回空。"""
    column, label = source
    if not row_ids or label not in {"采购分摊", "吉客云单据匹配", "销售订单"}:
        return {}
    numbers: dict[int, list[str]] = {}
    if label == "采购分摊":
        rows = (
            db.query(PurchaseAllocationItem.sku_id, PurchaseAllocationItem.source_item_id)
            .filter(PurchaseAllocationItem.sku_id.in_(row_ids))
            .all()
        )
        resolved = _inbound_reference_numbers(
            db,
            [source_item_id for _, source_item_id in rows if source_item_id is not None],
        )
        for sku_id, source_item_id in rows:
            if source_item_id is None:
                continue
            number = resolved.get(source_item_id)
            if number:
                bucket = numbers.setdefault(int(sku_id), [])
                if number not in bucket:
                    bucket.append(number)
    elif label == "销售订单":
        # sku_id 与 sku_code 双通道归属：销售明细多只带 sku_code 文本（sku_id 列常为空），编码按大小写不敏感匹配
        code_map = dict(
            db.query(ProductSku.id, ProductSku.sku_code)
            .filter(ProductSku.id.in_(row_ids))
            .all()
        )
        code_values = {code.lower() for code in code_map.values() if code}
        conditions = [SalesOrderItem.sku_id.in_(row_ids)]
        if code_values:
            conditions.append(func.lower(SalesOrderItem.sku_code).in_(code_values))
        rows = (
            db.query(SalesOrderItem.sku_id, SalesOrderItem.sku_code, SalesOrderItem.order_id)
            .filter(or_(*conditions))
            .all()
        )
        order_ids = {order_id for _, _, order_id in rows}
        order_nos = (
            dict(
                db.query(SalesOrder.id, SalesOrder.order_no)
                .filter(SalesOrder.id.in_(order_ids))
                .all()
            )
            if order_ids
            else {}
        )
        for sku_id, sku_code, order_id in rows:
            number = order_nos.get(order_id)
            if not number:
                continue
            owners: set[int] = set()
            if sku_id is not None and int(sku_id) in code_map:
                owners.add(int(sku_id))
            item_code = (sku_code or "").lower()
            if item_code:
                owners.update(
                    row_id for row_id, code in code_map.items() if code and code.lower() == item_code
                )
            for owner in owners:
                bucket = numbers.setdefault(owner, [])
                if number not in bucket:
                    bucket.append(number)
    else:
        rows = (
            db.query(JackyunGoodsDocumentItem.matched_sku_id, JackyunGoodsDocumentItem.id)
            .filter(JackyunGoodsDocumentItem.matched_sku_id.in_(row_ids))
            .all()
        )
        resolved = _inbound_reference_numbers(db, [item_id for _, item_id in rows])
        for sku_id, item_id in rows:
            number = resolved.get(item_id)
            if number:
                bucket = numbers.setdefault(int(sku_id), [])
                if number not in bucket:
                    bucket.append(number)
    return numbers


def _attach_reference_numbers(
    db: Session,
    sources: tuple[tuple[Any, str], ...],
    references: dict[int, list[dict[str, Any]]],
) -> None:
    """为引用计数补充占用单号；每项最多 5 个，超出的截断并以「等」收尾。"""
    source_by_label = {label: column for column, label in sources}
    for refs in references.values():
        for ref in refs:
            ref["numbers"] = []
    for label, column in source_by_label.items():
        row_ids = [
            row_id for row_id, refs in references.items()
            if any(ref["label"] == label for ref in refs)
        ]
        if not row_ids:
            continue
        numbers_by_id = _reference_numbers(db, (column, label), row_ids)
        if not numbers_by_id:
            continue
        for row_id, refs in references.items():
            for ref in refs:
                if ref["label"] != label:
                    continue
                numbers = numbers_by_id.get(row_id, [])
                ref["numbers"] = numbers[:5] + (["等"] if len(numbers) > 5 else [])


@router.post("/catalog/bulk-delete")
def bulk_delete_catalog(
    body: CatalogBulkDeleteBody,
    request: Request,
    _admin: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """批量删除单品和耗材档案；存在历史引用时只返回阻塞原因。"""
    requested: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for item in body.items:
        if item.id <= 0:
            raise HTTPException(400, "请提供有效的货品档案 ID")
        key = (item.kind, item.id)
        if key not in seen:
            seen.add(key)
            requested.append(key)
    if not requested or len(requested) > 500:
        raise HTTPException(400, "请提供 1 到 500 个货品档案")

    goods_ids = [row_id for kind, row_id in requested if kind == "goods"]
    consumable_ids = [row_id for kind, row_id in requested if kind == "consumable"]
    goods_by_id = {row.id: row for row in db.query(ProductSku).filter(ProductSku.id.in_(goods_ids)).all()} if goods_ids else {}
    consumable_by_id = {row.id: row for row in db.query(Consumable).filter(Consumable.id.in_(consumable_ids)).all()} if consumable_ids else {}
    goods_references = _catalog_reference_counts(db, _PRODUCT_SKU_REFERENCE_SOURCES, goods_ids)
    consumable_references = _catalog_reference_counts(db, _CONSUMABLE_REFERENCE_SOURCES, consumable_ids)
    # 补充占用单号（采购单号/入库单号），dry_run 与正式删除共用同一份组装结果。
    _attach_reference_numbers(db, _PRODUCT_SKU_REFERENCE_SOURCES, goods_references)
    _attach_reference_numbers(db, _CONSUMABLE_REFERENCE_SOURCES, consumable_references)

    blocked: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    not_found: list[dict[str, int | str]] = []
    deleted_items: list[dict[str, int | str]] = []
    for kind, row_id in requested:
        row = goods_by_id.get(row_id) if kind == "goods" else consumable_by_id.get(row_id)
        if row is None:
            not_found.append({"kind": kind, "id": row_id})
            continue
        if kind == "goods" and row.product_type != "single":
            invalid.append({"kind": kind, "id": row.id, "code": row.sku_code, "reason": "套装请在套装档案中操作"})
            continue
        references = (goods_references if kind == "goods" else consumable_references).get(row.id, [])
        code = row.sku_code if kind == "goods" else row.code
        if references:
            # 采购单号为纯数字；仅回退到入库单号（RK/CK 等前缀）时不视作被采购单占用
            occupied_by_purchase = any(
                ref["label"] in {"采购分摊", "吉客云单据匹配"}
                and any(str(number).isdigit() for number in ref.get("numbers", []))
                for ref in references
            )
            # 销售订单号来自 SalesOrder.order_no；「等」为截断占位符，不视作单号
            occupied_by_sales = any(
                ref["label"] == "销售订单"
                and any(str(number) not in {"", "等"} for number in ref.get("numbers", []))
                for ref in references
            )
            if occupied_by_purchase:
                reason = "该货品被采购单占用，不能删除"
            elif occupied_by_sales:
                reason = "该货品被销售订单占用，不能删除"
            else:
                reason = "已有历史业务引用，不能删除"
            blocked.append({
                "kind": kind,
                "id": row.id,
                "code": code,
                "reason": reason,
                "references": references,
            })
            continue
        deleted_items.append({"kind": kind, "id": row.id, "code": code})
        if not body.dry_run:
            db.delete(row)

    if not body.dry_run:
        db.commit()
        audit(
            db,
            current_actor(request),
            "catalog.bulk_delete",
            "catalog",
            None,
            {
                "requested": [{"kind": kind, "id": row_id} for kind, row_id in requested],
                "deletedItems": deleted_items,
                "blocked": blocked,
                "invalid": invalid,
                "notFound": not_found,
            },
        )
    return {
        "ok": True,
        "dryRun": body.dry_run,
        "requested": len(requested),
        "deleted": len(deleted_items),
        "deletedItems": deleted_items,
        "blocked": blocked,
        "invalid": invalid,
        "notFound": not_found,
    }


class CatalogBulkStatusBody(BaseModel):
    items: list[CatalogBulkDeleteItem]
    status: str


@router.post("/catalog/bulk-status")
def bulk_set_catalog_status(
    body: CatalogBulkStatusBody,
    request: Request,
    _admin: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """批量启用/停用单品与耗材档案；停用后不再参与新增业务，历史记录全部保留。"""
    if body.status not in {"active", "inactive"}:
        raise HTTPException(400, "状态仅支持 active（启用）或 inactive（停用）")
    requested: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for item in body.items:
        if item.id <= 0:
            raise HTTPException(400, "请提供有效的货品档案 ID")
        key = (item.kind, item.id)
        if key not in seen:
            seen.add(key)
            requested.append(key)
    if not requested or len(requested) > 500:
        raise HTTPException(400, "请提供 1 到 500 个货品档案")

    goods_ids = [row_id for kind, row_id in requested if kind == "goods"]
    consumable_ids = [row_id for kind, row_id in requested if kind == "consumable"]
    goods_by_id = {row.id: row for row in db.query(ProductSku).filter(ProductSku.id.in_(goods_ids)).all()} if goods_ids else {}
    consumable_by_id = {row.id: row for row in db.query(Consumable).filter(Consumable.id.in_(consumable_ids)).all()} if consumable_ids else {}

    updated_items: list[dict[str, Any]] = []
    not_found: list[dict[str, int | str]] = []
    for kind, row_id in requested:
        row = goods_by_id.get(row_id) if kind == "goods" else consumable_by_id.get(row_id)
        if row is None:
            not_found.append({"kind": kind, "id": row_id})
            continue
        row.status = body.status
        updated_items.append({
            "kind": kind,
            "id": row.id,
            "code": row.sku_code if kind == "goods" else row.code,
            "status": body.status,
        })
    db.commit()
    audit(
        db,
        current_actor(request),
        "catalog.bulk_status",
        "catalog",
        None,
        {
            "items": [{"kind": kind, "id": row_id} for kind, row_id in requested],
            "status": body.status,
            "updatedItems": updated_items,
            "notFound": not_found,
        },
    )
    return {"updated": len(updated_items), "items": updated_items, "notFound": not_found}


class BundleBulkDeleteBody(BaseModel):
    ids: list[int]
    dry_run: bool = False


_BUNDLE_REFERENCE_SOURCES = _PRODUCT_SKU_REFERENCE_SOURCES


@router.post("/catalog/bundles/bulk-delete")
def bulk_delete_bundles(
    body: BundleBulkDeleteBody,
    request: Request,
    _admin: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """批量删除未被历史业务引用的套装档案；有引用的档案只返回阻塞原因。"""
    ids = list(dict.fromkeys(body.ids))
    if not ids or len(ids) > 500 or any(row_id <= 0 for row_id in ids):
        raise HTTPException(400, "请提供 1 到 500 个有效的套装档案 ID")

    rows = db.query(ProductSku).filter(ProductSku.id.in_(ids)).all()
    by_id = {row.id: row for row in rows}
    not_found = [row_id for row_id in ids if row_id not in by_id]
    invalid = [
        {"id": row.id, "skuCode": row.sku_code, "reason": "仅可删除套装或虚拟组合套装"}
        for row in rows
        if row.product_type not in {"bundle", "virtual_bundle"}
    ]
    bundle_ids = [
        row_id for row_id in ids
        if row_id in by_id and by_id[row_id].product_type in {"bundle", "virtual_bundle"}
    ]

    references_by_id = _catalog_reference_counts(db, _BUNDLE_REFERENCE_SOURCES, bundle_ids)

    blocked: list[dict[str, Any]] = []
    deleted_ids: list[int] = []
    for row_id in bundle_ids:
        row = by_id[row_id]
        references = references_by_id[row_id]
        if references:
            blocked.append({
                "id": row.id,
                "skuCode": row.sku_code,
                "reason": "已有历史业务引用，不能删除",
                "references": references,
            })
            continue
        deleted_ids.append(row.id)
        if not body.dry_run:
            db.delete(row)

    if not body.dry_run:
        db.commit()
        audit(
            db,
            current_actor(request),
            "catalog.bundle.bulk_delete",
            "product_skus",
            None,
            {
                "requested": ids,
                "deletedIds": deleted_ids,
                "blocked": blocked,
                "invalid": invalid,
                "notFound": not_found,
            },
        )
    return {
        "ok": True,
        "dryRun": body.dry_run,
        "requested": len(ids),
        "deleted": len(deleted_ids),
        "deletedIds": deleted_ids,
        "blocked": blocked,
        "invalid": invalid,
        "notFound": not_found,
    }


@router.get("/orders")
def orders(status: str | None = None, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return dashboard.list_orders(db, status=status)


@router.get("/aftersales")
def aftersales(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return dashboard.list_aftersales(db)
