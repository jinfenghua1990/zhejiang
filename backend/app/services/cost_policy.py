from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.catalog import ProductSku
from app.models.jackyun import JackyunGoodsDocumentItem
from app.models.purchase import ExternalPurchaseOrder, PurchaseAllocationItem
from app.services.procurement_chain_service import amount_close
from app.services.procurement_chain_service import parse_inbound_doc_id


def _has_exact_inbound_fact(db: Session, row: PurchaseAllocationItem) -> bool:
    """历史备注没有 source_item_id 时，按入库单 + SKU/数量/单价做唯一核验。"""
    document_id = parse_inbound_doc_id(row.note)
    if document_id is None or row.sku_id is None or row.quantity is None or row.unit_price is None:
        return False
    matches = []
    for item in db.query(JackyunGoodsDocumentItem).filter_by(document_id=document_id).all():
        sku_match = item.matched_sku_id == row.sku_id or row.sku_code in {
            str(item.goods_no or "").strip(),
            str(item.sku_barcode or "").strip(),
        }
        if not sku_match or item.quantity is None:
            continue
        price = item.unit_price_tax
        if price is None and item.amount_tax is not None and item.quantity:
            price = item.amount_tax / item.quantity
        if price is None:
            continue
        if abs(item.quantity - row.quantity) <= Decimal("0.0001") and abs(price - row.unit_price) <= Decimal("0.0001"):
            matches.append(item.id)
    return len(matches) == 1


def allocation_cost_anomalies(db: Session, po: ExternalPurchaseOrder) -> list[str]:
    """核对尚未绑定入库事实的手工分配单价。

    已由采购入库明细反填、或带有 source_item_id 的分配行，成本事实已经来自
    入库单，不能再拿货品档案里的历史固定成本重复拦截采购流程。
    """
    rows = db.query(PurchaseAllocationItem).filter_by(po_id=po.id).all()
    anomalies: list[str] = []
    for row in rows:
        if (
            row.source == "inbound_auto"
            or row.source_item_id is not None
            or _has_exact_inbound_fact(db, row)
        ):
            continue
        if not row.sku_id or row.unit_price is None:
            continue
        sku = db.get(ProductSku, row.sku_id)
        if sku is None or sku.cost_mode == "dynamic" or sku.default_cost is None:
            continue
        tolerance = sku.cost_tolerance_pct if sku.cost_tolerance_pct is not None else Decimal("0.0200")
        if not amount_close(row.unit_price, sku.default_cost, tolerance):
            anomalies.append(f"{sku.sku_code}: 实际 {row.unit_price} vs 固定成本 {sku.default_cost}")
    return anomalies
