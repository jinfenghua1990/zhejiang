"""修复 product_skus.sku_name（存量全是"默认规格"）+ 回填采购成本 default_cost。

1. sku_name ← Product.goods_name（按 product_id 关联；无 product_id 的用入库单最新品名）
2. default_cost ← 入库单明细最近一次含税金额/入库数量（真实采购含税价）

幂等可重跑：sku_name 仅在为空或等于"默认规格"时覆盖（保留人工改过的名字）；
default_cost 始终用最新入库价覆盖（跟随最新采购成本）。
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal  # noqa: E402
from app.models.catalog import Product, ProductSku  # noqa: E402
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem  # noqa: E402

PLACEHOLDER_NAMES = {"", "默认规格", "默认", "-"}


def main() -> None:
    db = SessionLocal()
    try:
        products = {p.id: p for p in db.query(Product).all()}

        # 入库单明细：按 goods_no 聚合（最新品名 / 最新含税单价）
        docs = {d.id: d for d in db.query(JackyunGoodsDocument).filter_by(document_type="inbound").all()}
        latest_name: dict[str, str] = {}
        latest_cost: dict[str, Decimal] = {}
        latest_at: dict[str, object] = {}
        items = (
            db.query(JackyunGoodsDocumentItem)
            .join(JackyunGoodsDocument, JackyunGoodsDocumentItem.document_id == JackyunGoodsDocument.id)
            .filter(JackyunGoodsDocument.document_type == "inbound")
            .all()
        )
        for it in items:
            doc = docs.get(it.document_id)
            if not doc or not it.goods_no:
                continue
            at = doc.document_at
            prev_at = latest_at.get(it.goods_no)
            if prev_at is not None and at is not None and at <= prev_at:
                continue
            latest_at[it.goods_no] = at
            if it.goods_name:
                latest_name[it.goods_no] = it.goods_name
            if it.amount_tax is not None and it.quantity and it.quantity > 0:
                latest_cost[it.goods_no] = (it.amount_tax / it.quantity).quantize(Decimal("0.0001"))

        fixed_name = fixed_cost = 0
        for sku in db.query(ProductSku).all():
            # 1) sku_name：主档品名优先，入库单品名兜底；仅覆盖占位名，不动人工值
            product = products.get(sku.product_id) if sku.product_id else None
            target = (product.goods_name if product and product.goods_name else None) or latest_name.get(sku.sku_code)
            if target and (not sku.sku_name or sku.sku_name.strip() in PLACEHOLDER_NAMES) and sku.sku_name != target:
                sku.sku_name = target
                fixed_name += 1
            # 2) default_cost：最新采购含税价
            cost = latest_cost.get(sku.sku_code)
            if cost is not None:
                sku.default_cost = cost
                fixed_cost += 1

        db.commit()
        print(f"sku_name 修复 {fixed_name} 条；default_cost 回填 {fixed_cost} 条（最新采购含税价）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
