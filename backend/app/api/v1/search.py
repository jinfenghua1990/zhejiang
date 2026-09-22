
from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.catalog import Product, ProductSku
from app.models.purchase import ExternalPurchaseOrder, Supplier
from app.models.sales import SalesOrder

router = APIRouter(prefix="/search", tags=["search"])

_PER_GROUP = 6


@router.get("/global")
def global_search(
    q: str = Query(..., min_length=1, max_length=64),
    db: Session = Depends(get_db),
) -> dict[str, list[dict[str, str]]]:
    """全局搜索：货品 / 供应商 / 采购单 / 销售单，供顶部搜索框下拉建议。"""
    keyword = q.strip()
    if not keyword:
        return {"products": [], "suppliers": [], "purchases": [], "sales": []}
    like = f"%{keyword}%"

    sku_rows = (
        db.query(ProductSku, Product.goods_name)
        .outerjoin(Product, ProductSku.product_id == Product.id)
        .filter(
            or_(
                ProductSku.sku_code.ilike(like),
                ProductSku.sku_name.ilike(like),
                ProductSku.barcode.ilike(like),
                Product.goods_name.ilike(like),
            )
        )
        .limit(_PER_GROUP)
        .all()
    )
    products = [
        {
            "label": sku.sku_name or goods_name or sku.sku_code,
            "sub": sku.sku_code if sku.sku_code != (sku.sku_name or goods_name or "") else "",
        }
        for sku, goods_name in sku_rows
    ]

    suppliers = [
        {"label": s.name, "sub": s.platform}
        for s in db.query(Supplier)
        .filter(Supplier.name.ilike(like))
        .limit(_PER_GROUP)
        .all()
    ]

    purchases = [
        {"label": p.external_order_id, "sub": p.supplier_name}
        for p in db.query(ExternalPurchaseOrder)
        .filter(
            or_(
                ExternalPurchaseOrder.external_order_id.ilike(like),
                ExternalPurchaseOrder.supplier_name.ilike(like),
            )
        )
        .limit(_PER_GROUP)
        .all()
        if (p.raw or {}).get("referenceOnly") is not True
    ]

    sales = [
        {
            "label": o.order_no,
            "sub": " · ".join(part for part in (o.platform or "", f"¥{o.order_amount}" if o.order_amount is not None else "") if part),
        }
        for o in db.query(SalesOrder)
        .filter(SalesOrder.order_no.ilike(like))
        .limit(_PER_GROUP)
        .all()
    ]

    return {"products": products, "suppliers": suppliers, "purchases": purchases, "sales": sales}
