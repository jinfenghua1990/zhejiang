"""采购入库成本计算。

采购入库明细是本系统的成本事实来源。货品档案中的 ``default_cost`` 只作为
没有可用入库成本时的兜底，不参与已经有入库事实的加权平均计算。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.catalog import ProductSku
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.utils.money import to_decimal


def sales_sku_lookup(db: Session) -> dict[str, int]:
    """为销售明细建立安全的 SKU 编码索引。

    销售清单可能只保存了货品编号，没有写入内部 ``sku_id``。只有当一个
    编码/吉客云 SKU ID/条码唯一命中时才建立索引，重复编码不猜测归属。
    """
    candidates: dict[str, set[int]] = defaultdict(set)
    for sku in db.query(ProductSku).filter(ProductSku.status == "active").all():
        for value in (sku.sku_code, sku.jackyun_sku_id, sku.barcode):
            key = str(value or "").strip().lower()
            if key:
                candidates[key].add(sku.id)
    return {key: next(iter(ids)) for key, ids in candidates.items() if len(ids) == 1}


def resolve_sales_sku_id(
    sku_id: int | None,
    sku_code: str | None,
    lookup: dict[str, int],
) -> int | None:
    """优先使用销售明细已有的 SKU ID，否则按唯一编码补齐。"""
    if sku_id is not None:
        return int(sku_id)
    key = str(sku_code or "").strip().lower()
    return lookup.get(key) if key else None


def weighted_inbound_costs(
    db: Session,
    *,
    as_of: datetime | None = None,
    sku_ids: set[int] | None = None,
) -> dict[int, Decimal]:
    """按入库数量计算 SKU 加权平均含税采购成本。

    - 优先使用入库明细的 ``unit_price_tax``；缺失时用 ``amount_tax / quantity``；
    - 只纳入数量大于 0 且能识别到 SKU 的入库明细；
    - ``as_of`` 用于月度销售核算，避免把账期之后的入库价格带入历史月份；
    - 日期为空的历史入库仍纳入全量累计，避免旧文件因缺日期而丢失成本事实。
    """
    query = (
        db.query(JackyunGoodsDocumentItem)
        .join(
            JackyunGoodsDocument,
            JackyunGoodsDocument.id == JackyunGoodsDocumentItem.document_id,
        )
        .filter(
            JackyunGoodsDocument.document_type == "inbound",
            JackyunGoodsDocumentItem.matched_sku_id.isnot(None),
            JackyunGoodsDocumentItem.quantity.isnot(None),
            JackyunGoodsDocumentItem.quantity > 0,
        )
    )
    if sku_ids is not None and not sku_ids:
        return {}
    if sku_ids is not None:
        query = query.filter(JackyunGoodsDocumentItem.matched_sku_id.in_(sku_ids))
    if as_of is not None:
        query = query.filter(
            or_(
                JackyunGoodsDocument.document_at.is_(None),
                JackyunGoodsDocument.document_at < as_of,
            )
        )

    totals: dict[int, tuple[Decimal, Decimal]] = defaultdict(
        lambda: (Decimal("0"), Decimal("0"))
    )
    for item in query.all():
        quantity = to_decimal(item.quantity)
        if quantity <= 0 or item.matched_sku_id is None:
            continue
        if item.unit_price_tax is not None:
            unit_cost = to_decimal(item.unit_price_tax)
        elif item.amount_tax is not None:
            unit_cost = to_decimal(item.amount_tax) / quantity
        else:
            continue
        if not unit_cost.is_finite() or unit_cost < 0:
            continue
        total_quantity, total_amount = totals[int(item.matched_sku_id)]
        totals[int(item.matched_sku_id)] = (
            total_quantity + quantity,
            total_amount + quantity * unit_cost,
        )

    return {
        sku_id: (total_amount / total_quantity).quantize(Decimal("0.0001"))
        for sku_id, (total_quantity, total_amount) in totals.items()
        if total_quantity > 0
    }
