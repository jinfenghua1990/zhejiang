from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.db import get_db
from app.models.purchase import ExternalPurchaseOrder
from app.services import purchase_service
from app.services.procurement_consistency import (
    assign_shared_inbound_item,
    completion_snapshot,
    jackyun_group_summary,
)

router = APIRouter(prefix="/purchase", tags=["purchase-consistency"])


class InboundSplitAssignmentBody(BaseModel):
    po_id: int = Field(..., gt=0)
    allocation_id: int = Field(..., gt=0)
    quantity: Decimal = Field(..., gt=0)


class InboundSplitBody(BaseModel):
    source_item_id: int = Field(..., gt=0)
    assignments: list[InboundSplitAssignmentBody] = Field(..., min_length=2, max_length=50)


@router.get("/jackyun-groups/{jackyun_po_id}")
def get_merge_group(jackyun_po_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return jackyun_group_summary(db, jackyun_po_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/orders/{po_id}/closure")
def get_order_closure(po_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    po = db.get(ExternalPurchaseOrder, po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    return completion_snapshot(db, po)


@router.post("/inbound-split")
def split_shared_inbound(
    body: InboundSplitBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """同一吉客云入库明细按数量分配给同一个合并采购组中的多张来源订单。"""
    try:
        return assign_shared_inbound_item(
            db,
            source_item_id=body.source_item_id,
            assignments=[item.model_dump() for item in body.assignments],
            actor=current_actor(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/orders/{po_id}/allocations/{item_id}")
def remove_allocation_canonical(
    po_id: int,
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """采购 SKU 分配删除的规范路径；旧 query-string 路径继续保留兼容。"""
    po = db.get(ExternalPurchaseOrder, po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    try:
        purchase_service.remove_allocation(
            db, po, item_id, actor=current_actor(request)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "poId": po_id, "allocationId": item_id}
