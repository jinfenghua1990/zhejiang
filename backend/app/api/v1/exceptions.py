from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.core.audit import audit
from app.db import get_db
from app.models.ops import ExceptionRecord
from app.models.purchase import ExternalPurchaseOrder, PurchaseAllocationItem

router = APIRouter(prefix="/exceptions", tags=["exceptions"])

ALLOWED = {"pending", "confirmed", "ignored", "resolved"}


class StatusBody(BaseModel):
    status: str
    note: str = ""


def _confirm_purchase_payment_gap(
    db: Session,
    exception: ExceptionRecord,
    actor: str,
) -> dict[str, Any]:
    """确认金额差异：保留 1688 实付，并用确认后的差额完成采购内容。"""
    po_id = exception.ref_id or str((exception.detail or {}).get("poId") or "")
    try:
        po = db.get(ExternalPurchaseOrder, int(po_id))
    except (TypeError, ValueError):
        po = None
    if po is None:
        raise ValueError("关联采购订单不存在")

    allocation_total = sum(
        (Decimal(str(row.amount or 0)) for row in db.query(PurchaseAllocationItem).filter_by(po_id=po.id).all()),
        Decimal("0"),
    )
    paid_amount = po.effective_paid_amount
    if allocation_total <= 0 or paid_amount is None:
        raise ValueError("订单缺少可确认的入库金额或 1688 实付款")
    adjustment = (allocation_total - paid_amount).quantize(Decimal("0.0001"))
    if abs(adjustment) <= Decimal("0.01"):
        adjustment = Decimal("0")
    po.adjustment_amount = adjustment or None
    po.adjustment_note = (
        "异常确认：按 1688 实际付款，入库金额差额留作已确认调整"
        if adjustment
        else ""
    )
    if po.purchase_status in ("pending_refine", "draft"):
        from app.services.purchase_service import mark_refined

        mark_refined(db, po, actor=actor)
    else:
        db.commit()
    return {
        "poId": po.id,
        "orderNo": po.external_order_id,
        "purchaseStatus": po.purchase_status,
        "paidAmount": str(paid_amount),
        "inboundAmount": str(allocation_total),
        "adjustmentAmount": str(adjustment),
    }


@router.get("")
def list_exceptions(
    status: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    q = db.query(ExceptionRecord).order_by(ExceptionRecord.id.desc())
    if status:
        q = q.filter(ExceptionRecord.status == status)
    rows = q.limit(limit).offset(offset).all()
    return [
        {
            "id": r.id, "code": r.code, "type": r.type, "severity": r.severity,
            "title": r.title, "detail": r.detail, "status": r.status,
            "createdAt": r.created_at.isoformat() if r.created_at else None,
            "handledBy": r.handled_by, "note": r.note,
        }
        for r in rows
    ]


@router.post("/{exc_id}/status")
def change_status(exc_id: int, body: StatusBody, request: Request,
                  db: Session = Depends(get_db)) -> dict[str, Any]:
    if body.status not in ALLOWED:
        raise HTTPException(400, f"非法状态: {body.status}")
    row = db.get(ExceptionRecord, exc_id)
    if not row:
        raise HTTPException(404, "异常不存在")
    workflow = None
    if body.status == "confirmed" and row.code == "PURCHASE_PAYMENT_GAP":
        try:
            workflow = _confirm_purchase_payment_gap(db, row, current_actor(request))
        except ValueError as exc:
            db.rollback()
            raise HTTPException(400, str(exc))
    row.status = body.status
    actor = current_actor(request)
    row.handled_by = actor
    row.handled_at = datetime.now(timezone.utc)
    row.note = body.note
    db.commit()
    audit(db, actor, f"exception.{body.status}", "exceptions", exc_id, {"note": body.note})
    result: dict[str, Any] = {"ok": True, "id": exc_id, "status": row.status}
    if workflow is not None:
        result["workflow"] = workflow
    return result
