from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.db import get_db
from app.models.catalog import Product, ProductSku
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.models.production import ProductionOrder, ProductionOrderItem
from app.models.purchase import ExternalPurchaseOrder, PurchaseAllocationItem
from app.models.sales import SalesOrder, SalesOrderItem
from app.services.production_service import (
    PRODUCTION_STATUSES,
    cancel_production_order,
    create_production_order,
    list_production_orders,
    production_order_detail,
    recalculate_production_materials,
)
from app.utils.money import to_decimal
from app.services.inventory_position_service import _resolve_sku_id, _sku_lookup, current_positions
from app.services.inbound_document_view import list_inbound_documents
from app.services.sales_scope import deal_orders_condition

router = APIRouter(prefix="/supply-chain", tags=["supply-chain"])

OPEN_SUPPLY_STATUSES = {
    "confirmed",
    "jackyun_linked",
    "producing",
    "shipped",
    "arrived",
}
OPEN_PRODUCTION_STATUSES = {
    "planned",
    "confirmed",
    "producing",
    "produced",
    "shipped",
    "arrived",
    "inbound",
}


class ProductionItemInput(BaseModel):
    sku_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0)


class ProductionOrderCreateInput(BaseModel):
    factory_name: str = Field(min_length=1, max_length=256)
    planned_start_date: date | None = None
    expected_delivery_date: date | None = None
    source_type: str = Field(default="manual", max_length=32)
    note: str = Field(default="", max_length=2000)
    items: list[ProductionItemInput] = Field(min_length=1, max_length=200)


@router.get("/inbound-documents")
def inbound_documents(
    q: str = Query("", max_length=120),
    status: str = Query("", max_length=20),
    warehouse: str = Query("", max_length=256),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """按入库主单展示真实入库单、明细和采购关联。"""
    return list_inbound_documents(
        db,
        q=q,
        status=status,
        warehouse=warehouse,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )


def _qty(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{to_decimal(value):f}"


def _days(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value.quantize(Decimal('0.1')):f}"


def _valid_sales_condition():
    return deal_orders_condition()


@router.get("/replenishment")
def replenishment(
    days: int = Query(30, ge=7, le=180, description="销量观察周期"),
    lead_days: int = Query(14, ge=1, le=120, description="补货/生产交期天数"),
    safety_days: int = Query(7, ge=0, le=90, description="安全库存覆盖天数"),
    search: str = Query("", max_length=100),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """真实数据驱动的补货建议。

    数据来源：
    - 当前库存：吉客云最新库存快照；
    - 近销：本地销售订单明细 quantity，按 sales_scope 成交口径剔除关闭/取消/作废/待审核/退货/退款单；
    - 待供应：已确认尚未入库的采购数量 + 非取消生产单中尚未关联真实入库的生产数量。

    生产完成、工厂待发、成品在途和已到货待入库仍然属于“待供应”；只有关联真实吉客云
    入库后才从生产待供应中退出，避免生产完成瞬间重复触发补货。

    建议补货 = max(日均销量 × (交期天数 + 安全天数) - 当前库存 - 待供应, 0)。
    无出入库单据或观察期无销量时不伪造建议数量，返回 null 并给出原因。
    """
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    positions = current_positions(db)
    last_document_at = positions["last_document_at"]
    inventory_by_sku = positions["by_sku"]

    # 近销优先使用真实销售出库单；仅对尚无匹配出库单的 SKU 回退到销售订单。
    # 这样不会把同一笔销售同时计入两套来源。
    sku_lookup, sku_by_id = _sku_lookup(db)
    outbound_rows = (
        db.query(JackyunGoodsDocumentItem, JackyunGoodsDocument)
        .join(JackyunGoodsDocument, JackyunGoodsDocument.id == JackyunGoodsDocumentItem.document_id)
        .filter(
            JackyunGoodsDocument.document_type == "outbound",
            JackyunGoodsDocument.document_at >= since,
        )
        .all()
    )
    outbound_by_sku: dict[int, Decimal] = {}
    outbound_document_ids: set[int] = set()
    matched_outbound_items = 0
    unmatched_outbound_items = 0
    for item, document in outbound_rows:
        quantity = to_decimal(item.quantity)
        if quantity <= 0:
            continue
        outbound_document_ids.add(document.id)
        sku_id = _resolve_sku_id(item, sku_lookup, sku_by_id)
        if sku_id is None:
            unmatched_outbound_items += 1
            continue
        outbound_by_sku[sku_id] = outbound_by_sku.get(sku_id, Decimal("0")) + quantity
        matched_outbound_items += 1

    sales_rows = (
        db.query(
            SalesOrderItem.sku_id,
            SalesOrderItem.sku_code,
            func.coalesce(func.sum(SalesOrderItem.quantity), 0),
        )
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
        .filter(SalesOrder.ordered_at >= since, _valid_sales_condition())
        .group_by(SalesOrderItem.sku_id, SalesOrderItem.sku_code)
        .all()
    )
    sales_by_id: dict[int, Decimal] = {}
    sales_by_code: dict[str, Decimal] = {}
    for sku_id, sku_code, quantity in sales_rows:
        qty = to_decimal(quantity)
        if sku_id is not None:
            sales_by_id[int(sku_id)] = sales_by_id.get(int(sku_id), Decimal("0")) + qty
        elif sku_code:
            sales_by_code[sku_code] = sales_by_code.get(sku_code, Decimal("0")) + qty

    purchase_open_rows = (
        db.query(
            PurchaseAllocationItem.sku_id,
            PurchaseAllocationItem.sku_code,
            func.coalesce(func.sum(PurchaseAllocationItem.quantity), 0),
        )
        .join(ExternalPurchaseOrder, ExternalPurchaseOrder.id == PurchaseAllocationItem.po_id)
        .filter(ExternalPurchaseOrder.purchase_status.in_(OPEN_SUPPLY_STATUSES))
        .group_by(PurchaseAllocationItem.sku_id, PurchaseAllocationItem.sku_code)
        .all()
    )
    purchase_open_by_id: dict[int, Decimal] = {}
    purchase_open_by_code: dict[str, Decimal] = {}
    for sku_id, sku_code, quantity in purchase_open_rows:
        qty = to_decimal(quantity)
        if sku_id is not None:
            purchase_open_by_id[int(sku_id)] = purchase_open_by_id.get(int(sku_id), Decimal("0")) + qty
        elif sku_code:
            purchase_open_by_code[sku_code] = purchase_open_by_code.get(sku_code, Decimal("0")) + qty

    production_open_rows = (
        db.query(
            ProductionOrderItem.sku_id,
            ProductionOrderItem.sku_code,
            func.coalesce(
                func.sum(ProductionOrderItem.quantity - ProductionOrderItem.inbound_qty),
                0,
            ),
        )
        .join(ProductionOrder, ProductionOrder.id == ProductionOrderItem.production_order_id)
        .filter(
            ProductionOrder.status.in_(OPEN_PRODUCTION_STATUSES),
            ProductionOrderItem.quantity > ProductionOrderItem.inbound_qty,
        )
        .group_by(ProductionOrderItem.sku_id, ProductionOrderItem.sku_code)
        .all()
    )
    production_open_by_id: dict[int, Decimal] = {}
    production_open_by_code: dict[str, Decimal] = {}
    for sku_id, sku_code, quantity in production_open_rows:
        qty = to_decimal(quantity)
        if sku_id is not None:
            production_open_by_id[int(sku_id)] = production_open_by_id.get(int(sku_id), Decimal("0")) + qty
        elif sku_code:
            production_open_by_code[sku_code] = production_open_by_code.get(sku_code, Decimal("0")) + qty

    query = (
        db.query(ProductSku, Product)
        .outerjoin(Product, Product.id == ProductSku.product_id)
        .filter(ProductSku.status == "active")
        .order_by(ProductSku.sku_code, ProductSku.id)
    )
    term = search.strip()
    if term:
        pattern = f"%{term}%"
        query = query.filter(or_(
            ProductSku.sku_code.ilike(pattern),
            ProductSku.sku_name.ilike(pattern),
            ProductSku.barcode.ilike(pattern),
            Product.goods_name.ilike(pattern),
        ))

    rows: list[dict[str, Any]] = []
    urgent_count = 0
    attention_count = 0
    suggested_count = 0

    for sku, product in query.limit(limit).all():
        outbound_sold = outbound_by_sku.get(sku.id, Decimal("0"))
        if outbound_sold > 0:
            sold = outbound_sold
            sales_source = "sales_outbound"
        else:
            sold = sales_by_id.get(sku.id, sales_by_code.get(sku.sku_code, Decimal("0")))
            sales_source = "sales_order_fallback" if sold > 0 else "none"
        purchase_open = purchase_open_by_id.get(
            sku.id,
            purchase_open_by_code.get(sku.sku_code, Decimal("0")),
        )
        production_open = production_open_by_id.get(
            sku.id,
            production_open_by_code.get(sku.sku_code, Decimal("0")),
        )
        open_supply = purchase_open + production_open
        current = inventory_by_sku.get(sku.id)
        avg_daily = sold / Decimal(days) if sold > 0 else Decimal("0")

        target_stock: Decimal | None = None
        suggested: Decimal | None = None
        current_cover: Decimal | None = None
        effective_cover: Decimal | None = None
        stockout_date: str | None = None

        if current is None:
            risk = "no_data"
            reason = "该 SKU 没有任何出入库单据，暂不计算补货数量"
        elif avg_daily <= 0:
            risk = "no_sales"
            reason = f"近 {days} 天没有有效销量，暂不自动给出补货数量"
        else:
            target_stock = avg_daily * Decimal(lead_days + safety_days)
            effective_stock = current + open_supply
            suggested = max(target_stock - effective_stock, Decimal("0"))
            current_cover = current / avg_daily
            effective_cover = effective_stock / avg_daily
            if current_cover >= 0:
                stockout_date = (now + timedelta(days=float(current_cover))).date().isoformat()

            if current_cover < Decimal(lead_days):
                risk = "urgent"
                reason = "当前库存覆盖天数低于补货/生产交期"
                urgent_count += 1
            elif effective_cover < Decimal(lead_days + safety_days):
                risk = "attention"
                reason = "当前库存加待供应仍低于目标覆盖天数"
                attention_count += 1
            else:
                risk = "ok"
                reason = "当前库存与待供应可覆盖目标周期"
            if suggested > 0:
                suggested_count += 1

        rows.append({
            "skuId": sku.id,
            "skuCode": sku.sku_code,
            "skuName": sku.sku_name or "",
            "goodsName": product.goods_name if product else "",
            "barcode": sku.barcode or "",
            "unit": sku.unit or "",
            "currentInventory": _qty(current),
            "soldQuantity": _qty(sold),
            "salesSource": sales_source,
            "averageDailySales": _qty(avg_daily),
            "purchaseOpenSupplyQuantity": _qty(purchase_open),
            "productionOpenSupplyQuantity": _qty(production_open),
            "openSupplyQuantity": _qty(open_supply),
            "targetStock": _qty(target_stock),
            "suggestedReplenishment": _qty(suggested),
            "currentCoverDays": _days(current_cover),
            "effectiveCoverDays": _days(effective_cover),
            "estimatedStockoutDate": stockout_date,
            "risk": risk,
            "reason": reason,
        })

    return {
        "policy": {
            "salesWindowDays": days,
            "leadDays": lead_days,
            "safetyDays": safety_days,
            "targetCoverDays": lead_days + safety_days,
            "formula": "日均销量 × (交期天数 + 安全天数) - 当前库存 - 待供应",
        },
        "data": {
            "lastDocumentAt": last_document_at.isoformat() if last_document_at is not None else None,
            "inventoryPositionSource": positions["source"],
            "salesSince": since.isoformat(),
            "outboundDocumentCount": len(outbound_document_ids),
            "matchedOutboundItemCount": matched_outbound_items,
            "unmatchedOutboundItemCount": unmatched_outbound_items,
            "generatedAt": now.isoformat(),
        },
        "summary": {
            "skuCount": len(rows),
            "urgentCount": urgent_count,
            "attentionCount": attention_count,
            "suggestedCount": suggested_count,
        },
        "rows": rows,
    }


@router.get("/production-orders")
def production_orders(
    status: str = Query("", max_length=24),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if status and status not in PRODUCTION_STATUSES:
        raise HTTPException(status_code=400, detail="生产单状态不正确")
    rows = list_production_orders(db, status=status, limit=limit)
    return {
        "rows": rows,
        "summary": {
            "count": len(rows),
            "shortageCount": sum(1 for row in rows if row["materialShortageCount"] > 0),
        },
    }


@router.get("/production-orders/{order_id}")
def production_order(order_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    order = db.get(ProductionOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="生产单不存在")
    return production_order_detail(db, order)


@router.post("/production-orders")
def create_production(
    payload: ProductionOrderCreateInput,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        order = create_production_order(
            db,
            factory_name=payload.factory_name,
            items=[{"sku_id": item.sku_id, "quantity": item.quantity} for item in payload.items],
            actor=current_actor(request),
            planned_start_date=payload.planned_start_date,
            expected_delivery_date=payload.expected_delivery_date,
            source_type=payload.source_type,
            note=payload.note,
        )
        return production_order_detail(db, order)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/production-orders/{order_id}/recalculate-materials")
def recalculate_materials(order_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        order = recalculate_production_materials(db, order_id)
        return production_order_detail(db, order)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/production-orders/{order_id}/cancel")
def cancel_production(order_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        order = cancel_production_order(db, order_id)
        return production_order_detail(db, order)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
