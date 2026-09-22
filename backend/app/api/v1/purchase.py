from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, exists, or_
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.config import settings
from app.core.audit import audit
from app.db import get_db
from app.models.alibaba1688_import import Alibaba1688Order
from app.models.catalog import ProductSku
from app.models.purchase import (
    ExternalPurchaseOrder,
    JackyunPurchaseOrderLink,
    PurchaseAllocationItem,
    PurchaseExtraExpense,
    PurchaseInvoice,
    PurchaseInvoiceLink,
)
from app.services import purchase_invoice_truth_service as invoice_truth
from app.services import purchase_service as svc
from app.services.allocation import balance_check
from app.services.procurement_chain_service import _source_pairs

router = APIRouter(prefix="/purchase", tags=["purchase"])


def _po_or_404(db: Session, po_id: int) -> ExternalPurchaseOrder:
    po = svc.get_po(db, po_id)
    if not po:
        raise HTTPException(404, "采购订单不存在")
    return po


def _balance(db: Session, po: ExternalPurchaseOrder) -> dict[str, Any]:
    bal = svc.balance_of(db, po)
    return {k: (str(v) if hasattr(v, "quantize") else v) for k, v in bal.items()}


class CreatePOBody(BaseModel):
    external_order_id: str = Field(..., min_length=1)
    platform: str = "1688"
    supplier_name: str = ""
    title: str = ""
    ordered_at: datetime | None = None
    order_amount: str | None = None
    paid_amount: str | None = None
    buyer_account: str = ""
    warehouse_id: int | None = None
    # 货品类型（goods=正品 / consumable=耗材）：录入时选了就落 order_kind_override，
    # 不选则由工作台自动判定（HC 单/耗材档案/供应商关键词）。
    order_kind: str = Field("", pattern="^(|goods|consumable)$")


class UpdatePOBody(BaseModel):
    platform: str | None = None
    supplier_name: str | None = None
    title: str | None = None
    ordered_at: datetime | None = None
    order_amount: str | None = None
    paid_amount: str | None = None
    buyer_account: str | None = None
    warehouse_id: int | None = None


@router.get("/orders")
def list_orders(
    status: str | None = None,
    platform: str | None = None,
    q: str = "",
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    query = db.query(ExternalPurchaseOrder).order_by(ExternalPurchaseOrder.id.desc())
    if status:
        query = query.filter(ExternalPurchaseOrder.purchase_status == status)
    if platform:
        if platform == "non_1688":
            query = query.filter(ExternalPurchaseOrder.platform != "1688")
        else:
            try:
                query = query.filter(ExternalPurchaseOrder.platform == svc.normalize_platform(platform))
            except ValueError as exc:
                raise HTTPException(400, str(exc))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            ExternalPurchaseOrder.external_order_id.ilike(like)
            | ExternalPurchaseOrder.supplier_name.ilike(like)
            | ExternalPurchaseOrder.title.ilike(like)
        )
    # 分页前在数据库层排除两类“非真实采购主单”，避免先 limit/offset 后 Python 剔除
    # 导致页面条数不足、翻页漏单。
    deleted_source = exists().where(and_(
        Alibaba1688Order.external_order_id == ExternalPurchaseOrder.external_order_id,
        Alibaba1688Order.row_status == "deleted",
    ))
    active_source = exists().where(and_(
        Alibaba1688Order.external_order_id == ExternalPurchaseOrder.external_order_id,
        Alibaba1688Order.row_status != "deleted",
    ))
    query = query.filter(
        or_(
            ExternalPurchaseOrder.platform != "1688",
            ~deleted_source,
            active_source,
        ),
        or_(
            ExternalPurchaseOrder.raw["referenceOnly"].astext.is_(None),
            ExternalPurchaseOrder.raw["referenceOnly"].astext != "true",
        ),
    )
    pos = query.limit(limit).offset(offset).all()
    po_ids = [po.id for po in pos]
    allocations_by_po: dict[int, list[PurchaseAllocationItem]] = {}
    for item in db.query(PurchaseAllocationItem).filter(
        PurchaseAllocationItem.po_id.in_(po_ids)
    ).all() if po_ids else []:
        allocations_by_po.setdefault(item.po_id, []).append(item)
    expenses_by_po: dict[int, list[PurchaseExtraExpense]] = {}
    for expense in db.query(PurchaseExtraExpense).filter(
        PurchaseExtraExpense.po_id.in_(po_ids)
    ).all() if po_ids else []:
        expenses_by_po.setdefault(expense.po_id, []).append(expense)
    out = []
    for po in pos:
        bal = balance_check(
            po.effective_paid_amount,
            [item.amount for item in allocations_by_po.get(po.id, [])],
            [expense.amount for expense in expenses_by_po.get(po.id, [])],
        )
        invoice_summary = invoice_truth.purchase_invoice_truth(db, external=po)
        out.append({
            "id": po.id, "externalOrderId": po.external_order_id, "supplierName": po.supplier_name,
            "platform": po.platform or "other", "buyerAccount": po.buyer_account or "",
            "title": po.title, "paidAmount": str(po.paid_amount) if po.paid_amount is not None else None,
            "orderAmount": str(po.order_amount) if po.order_amount is not None else None,
            "orderedAt": po.ordered_at.isoformat() if po.ordered_at else None,
            "orderStatus": po.order_status or "",
            "purchaseStatus": po.purchase_status,
            "invoiceStatus": invoice_summary["status"],
            "invoicedAmount": str(invoice_summary["invoicedAmount"]),
            "invoiceOutstanding": (
                str(invoice_summary["outstandingAmount"])
                if invoice_summary["outstandingAmount"] is not None else None
            ),
            "invoiceNeedsReview": invoice_summary["needsReview"],
            "invoiceReviewReasons": invoice_summary["reviewReasons"],
            "goodsAllocated": str(bal["goods_allocated"]), "expenseAllocated": str(bal["expense_allocated"]),
            "unallocated": str(bal["unallocated"]), "balanced": bal["balanced"],
            "taxInvoiceCount": invoice_summary["taxInvoiceCount"],
            "source": (po.raw or {}).get("source") or "manual",
            "sourceImportId": (po.raw or {}).get("sourceImportId"),
        })
    return out


@router.post("/orders")
def create_order(body: CreatePOBody, request: Request,
                 db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        po = svc.create_external_po(
            db, external_order_id=body.external_order_id, supplier_name=body.supplier_name,
            title=body.title, ordered_at=body.ordered_at, order_amount=body.order_amount,
            paid_amount=body.paid_amount, buyer_account=body.buyer_account,
            warehouse_id=body.warehouse_id,
            platform=body.platform,
            actor=current_actor(request),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if body.order_kind and po.order_kind_override != body.order_kind:
        po.order_kind_override = body.order_kind
        db.commit()
        audit(db, current_actor(request), "purchase.order.kind_override", "external_purchase_orders",
              po.id, detail={"kind": body.order_kind, "note": "手工录入时指定货品类型"})
    source = next((o for o, ext in _source_pairs(db) if ext and ext.id == po.id), None)
    return {"id": po.id, "workbenchOrderId": source.id if source else -po.id,
            "externalOrderId": po.external_order_id, "purchaseStatus": po.purchase_status}


@router.patch("/orders/{po_id}")
def update_order(po_id: int, body: UpdatePOBody, request: Request,
                 db: Session = Depends(get_db)) -> dict[str, Any]:
    """维护外部采购订单主档；订单号固定，避免改坏已有链路。"""
    po = _po_or_404(db, po_id)
    try:
        svc.update_external_po(
            db, po, actor=current_actor(request), **body.model_dump(exclude_unset=True)
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "id": po.id, "externalOrderId": po.external_order_id, "platform": po.platform}


class AllocationBody(BaseModel):
    sku_id: int | None = None
    sku_code: str = ""
    goods_name: str = ""
    quantity: str
    # 新建/编辑时优先填写总价，单价由后端按总价 / 数量计算；保留 unit_price 兼容旧调用方。
    unit_price: str | None = None
    amount: str | None = None
    note: str = ""


@router.post("/orders/{po_id}/allocations")
def add_allocation(po_id: int, body: AllocationBody, request: Request,
                   db: Session = Depends(get_db)) -> dict[str, Any]:
    po = _po_or_404(db, po_id)
    try:
        row = svc.add_allocation(db, po, sku_id=body.sku_id, sku_code=body.sku_code,
                                 goods_name=body.goods_name, quantity=body.quantity,
                                 unit_price=body.unit_price, amount=body.amount, note=body.note,
                                 actor=current_actor(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": row.id, "amount": str(row.amount), "balance": _balance(db, po)}


@router.delete("/allocations/{item_id}")
def remove_allocation(po_id: int, item_id: int, request: Request,
                      db: Session = Depends(get_db)) -> dict[str, Any]:
    po = _po_or_404(db, po_id)
    try:
        svc.remove_allocation(db, po, item_id, actor=current_actor(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "balance": _balance(db, po)}


@router.put("/orders/{po_id}/allocations/{item_id}")
def update_allocation(po_id: int, item_id: int, body: AllocationBody, request: Request,
                      db: Session = Depends(get_db)) -> dict[str, Any]:
    po = _po_or_404(db, po_id)
    try:
        # 编辑时保留原备注（add_allocation 仅新建写 note），故排除 body.note 防重复 kwarg
        row = svc.add_allocation(db, po, **body.model_dump(exclude={"note"}),
                                 allocation_id=item_id, note="", actor=current_actor(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": row.id, "amount": str(row.amount), "balance": _balance(db, po)}


class AdjustmentBody(BaseModel):
    adjustment_amount: str = ""
    note: str = ""


@router.post("/orders/{po_id}/adjustment")
def set_po_adjustment(po_id: int, body: AdjustmentBody, request: Request,
                      db: Session = Depends(get_db)) -> dict[str, Any]:
    """1688 微调金额：红包等导致开票金额与订单实付的零头差；传空清除。"""
    po = _po_or_404(db, po_id)
    try:
        svc.set_adjustment(db, po, amount=body.adjustment_amount, note=body.note,
                           actor=current_actor(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True,
            "adjustmentAmount": str(po.adjustment_amount) if po.adjustment_amount is not None else None,
            "balance": _balance(db, po)}


@router.post("/orders/{po_id}/reopen")
def reopen_order(po_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    po = _po_or_404(db, po_id)
    svc.reopen_purchase(db, po, actor=current_actor(request))
    return {"ok": True, "purchaseStatus": po.purchase_status}


class ExpenseBody(BaseModel):
    expense_type: str
    amount: str
    note: str = ""


@router.post("/orders/{po_id}/expenses")
def add_expense(po_id: int, body: ExpenseBody, request: Request,
                db: Session = Depends(get_db)) -> dict[str, Any]:
    po = _po_or_404(db, po_id)
    try:
        row = svc.add_expense(db, po, expense_type=body.expense_type, amount=body.amount,
                              note=body.note, actor=current_actor(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": row.id, "amount": str(row.amount), "balance": _balance(db, po)}


@router.delete("/expenses/{expense_id}")
def remove_expense(po_id: int, expense_id: int, request: Request,
                   db: Session = Depends(get_db)) -> dict[str, Any]:
    po = _po_or_404(db, po_id)
    try:
        svc.remove_expense(db, po, expense_id, actor=current_actor(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "balance": _balance(db, po)}


@router.get("/orders/{po_id}/balance")
def get_balance(po_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    po = _po_or_404(db, po_id)
    return _balance(db, po)


@router.post("/orders/{po_id}/refine")
def refine(po_id: int, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    po = _po_or_404(db, po_id)
    try:
        bal = svc.mark_refined(db, po, actor=current_actor(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "purchaseStatus": po.purchase_status, "balance": {k: (str(v) if hasattr(v, "quantize") else v) for k, v in bal.items()}}


class ConfirmAndResyncBody(BaseModel):
    note: str = ""


def _resync_jky_web_stockin(db: Session, *, required: bool = False) -> dict[str, Any]:
    """按需同步吉客云入库单；未配置时，确认采购内容不阻塞本地流程。"""
    resync: dict[str, Any] = {"attempted": False, "ok": None, "error": None}
    try:
        from app.adapters.jky_web import JkyWebClient, JkyWebError, JkyWebSessionError
        from app.services.jky_web_sync_service import sync_stockin
        if not settings.JKY_WEB_SIGN_SECRET:
            if required:
                resync.update({"attempted": True, "ok": False, "error": "JKY_WEB_SIGN_SECRET 未配置，无法回写"})
            else:
                resync["skippedReason"] = "吉客云入库同步未配置，已按本系统入库流程跳过"
            return resync
        resync["attempted"] = True
        try:
            client = JkyWebClient(db)
            from datetime import datetime, timedelta, timezone
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=7)
            result = sync_stockin(db, client, start, end)
            resync.update({"ok": True, "stats": result,
                           "window": {"start": start.isoformat(), "end": end.isoformat()}})
        except JkyWebSessionError as exc:
            resync.update({"ok": False, "error": f"登录态失效：{exc.message}"})
        except JkyWebError as exc:
            resync.update({"ok": False, "error": f"{exc.message}（kind={exc.kind}）"})
    except Exception as exc:
        resync.update({"ok": False, "error": f"回写异常：{exc!s}"})
    return resync


@router.post("/orders/{po_id}/confirm-and-resync")
def confirm_and_resync(po_id: int, body: ConfirmAndResyncBody | None,
                       request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """整单总确认：金额/耗材/吉客云 PO 关联校验 → 推进状态 → 可选同步本地入库事实。
    返回 {ok, status, balance, resync: {ok, ...}}，前端可展示同步结果。
    """
    po = _po_or_404(db, po_id)
    actor = current_actor(request)
    note = (body.note if body else "") or ""
    try:
        bal = svc.mark_refined(db, po, actor=actor)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    resync = _resync_jky_web_stockin(db)
    audit(db, actor, "purchase.po.confirm_and_resync", "external_purchase_orders", po.id,
          {"note": note, "resync": resync})
    return {
        "ok": True,
        "purchaseStatus": po.purchase_status,
        "balance": {k: (str(v) if hasattr(v, "quantize") else v) for k, v in bal.items()},
        "resync": resync,
    }


@router.post("/orders/{po_id}/resync-jky-web")
def resync_jky_web(po_id: int, request: Request,
                   db: Session = Depends(get_db)) -> dict[str, Any]:
    """仅回写本地（无需确认通过）：同步触发 jky_web stockin 拉取，验证回写链路。
    用于 confirm 卡住（金额/耗材异常）时单独测试回写是否通畅。
    """
    po = _po_or_404(db, po_id)
    actor = current_actor(request)
    resync = _resync_jky_web_stockin(db, required=True)
    audit(db, actor, "purchase.po.resync_jky_web", "external_purchase_orders", po.id, {"resync": resync})
    return {"ok": True, "purchaseStatus": po.purchase_status, "resync": resync}


class StatusBody(BaseModel):
    status: str


@router.post("/orders/{po_id}/status")
def advance(po_id: int, body: StatusBody, request: Request,
            db: Session = Depends(get_db)) -> dict[str, Any]:
    po = _po_or_404(db, po_id)
    try:
        svc.advance_status(db, po, body.status, actor=current_actor(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "purchaseStatus": po.purchase_status}


class JackyunLinkBody(BaseModel):
    purch_no: str
    replace_link_id: int | None = None
    relation_kind: str = ""  # ''/merged/split
    alloc_amount: Decimal | None = None
    note: str = ""


@router.post("/orders/{po_id}/jackyun-link")
def jackyun_link(po_id: int, body: JackyunLinkBody, request: Request,
                 db: Session = Depends(get_db)) -> dict[str, Any]:
    po = _po_or_404(db, po_id)
    try:
        svc.link_jackyun_po(db, po, body.purch_no, actor=current_actor(request),
                            replace_link_id=body.replace_link_id,
                            relation_kind=body.relation_kind,
                            alloc_amount=body.alloc_amount, note=body.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "purchaseStatus": po.purchase_status}


@router.delete("/orders/{po_id}/jackyun-links/{link_id}")
def unlink_jackyun_order(po_id: int, link_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    po = _po_or_404(db, po_id)
    link = db.get(JackyunPurchaseOrderLink, link_id)
    if link is None or link.po_id != po.id:
        raise HTTPException(404, "当前订单的采购单关联不存在")
    try:
        return svc.unlink_jackyun_po_link(db, po, link, actor=current_actor(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/orders/auto-link-purchase-orders")
def auto_link_purchase_orders(
    request: Request,
    dry_run: bool = Query(False, description="true=只预览候选，不落库不改状态"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """批量自动关联吉客云采购单到已确认 1688 订单（供应商+金额+时间唯一命中即关联）。

    与手动「关联采购单」同语义：关联后订单 confirmed -> jackyun_linked。
    幂等可重复触发；合并/拆分等一对多场景不自动关联，留待人工标注。
    """
    try:
        return svc.auto_link_purchase_orders(db, actor=current_actor(request), dry_run=dry_run)
    except Exception as exc:
        raise HTTPException(400, str(exc))


@router.post("/orders/batch-bypass-jackyun-po")
def batch_bypass_jackyun_po(
    request: Request,
    dry_run: bool = Query(False, description="true=只预览候选，不落库不改状态"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """批量放行「待生成采购单」订单（Excel 金额口径）。

    吉客云侧无采购单体系、金额以导入表格为准、入库已闭环时，
    采购单步骤按口径跳过（confirmed -> jackyun_linked），留痕可审计、可解除。
    """
    try:
        return svc.bypass_jackyun_po_inbound_closed(db, actor=current_actor(request), dry_run=dry_run)
    except Exception as exc:
        raise HTTPException(400, str(exc))


@router.post("/invoices/purge-voided-links")
def purge_voided_invoice_links(
    request: Request,
    dry_run: bool = Query(True, description="true=只预览将被清理的作废票关联，不落库"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """清理建立在作废发票（已红冲蓝字票 / 红字负数票）上的采购关联。

    作废票不对应真实采购，早期自动匹配未过滤它们，会虚增订单票面金额。
    """
    from app.services import procurement_chain_service as chain

    try:
        return chain.purge_voided_invoice_links(db, actor=current_actor(request), dry_run=dry_run)
    except Exception as exc:
        raise HTTPException(400, str(exc))


class InvoiceBody(BaseModel):
    invoice_no: str = ""
    invoice_amount: str
    invoice_date: datetime | None = None
    supplier_name: str = ""


class OrderInvoiceBody(InvoiceBody):
    allocated_amount: str | None = None


@router.post("/orders/{po_id}/invoices")
def register_order_invoice(po_id: int, body: OrderInvoiceBody, request: Request,
                           db: Session = Depends(get_db)) -> dict:
    po = _po_or_404(db, po_id)
    try:
        link = svc.register_order_invoice(db, po, **body.model_dump(), actor=current_actor(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    summary = invoice_truth.purchase_invoice_truth(db, external=po)
    return {
        "id": link.id,
        "invoiceId": link.invoice_id,
        "invoiceStatus": summary["status"],
        "invoicedAmount": str(summary["invoicedAmount"]),
        "invoiceOutstanding": (
            str(summary["outstandingAmount"])
            if summary["outstandingAmount"] is not None else None
        ),
    }


@router.delete("/invoice-links/{link_id}")
def unlink_registered_invoice(link_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    link = db.get(PurchaseInvoiceLink, link_id)
    if link is None:
        raise HTTPException(404, "发票关联不存在")
    po = _po_or_404(db, link.po_id)
    db.delete(link)
    db.flush()
    summary = invoice_truth.purchase_invoice_truth(db, external=po)
    po.invoice_status = invoice_truth.compatibility_invoice_status(summary["status"])
    db.commit()
    audit(db, current_actor(request), "purchase.invoice.unlink", "purchase_invoice_links", link_id, {"poId": po.id})
    return {
        "ok": True,
        "invoiceStatus": summary["status"],
        "invoicedAmount": str(summary["invoicedAmount"]),
        "invoiceOutstanding": (
            str(summary["outstandingAmount"])
            if summary["outstandingAmount"] is not None else None
        ),
    }


@router.post("/invoices")
def create_invoice(body: InvoiceBody, request: Request,
                   db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        row = svc.create_invoice(db, invoice_no=body.invoice_no, invoice_amount=body.invoice_amount,
                                 invoice_date=body.invoice_date, supplier_name=body.supplier_name,
                                 actor=current_actor(request))
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    return {"id": row.id, "invoiceNo": row.invoice_no, "invoiceAmount": str(row.invoice_amount)}


class InvoiceLinkBody(BaseModel):
    po_id: int
    allocated_amount: str


@router.post("/invoices/{invoice_id}/links")
def link_invoice(invoice_id: int, body: InvoiceLinkBody, request: Request,
                 db: Session = Depends(get_db)) -> dict[str, Any]:
    invoice = db.get(PurchaseInvoice, invoice_id)
    if not invoice:
        raise HTTPException(404, "发票不存在")
    po = _po_or_404(db, body.po_id)
    try:
        svc.link_invoice(db, invoice, po, body.allocated_amount,
                         actor=current_actor(request))
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    summary = invoice_truth.purchase_invoice_truth(db, external=po)
    return {
        "ok": True,
        "invoiceStatus": summary["status"],
        "invoicedAmount": str(summary["invoicedAmount"]),
        "invoiceOutstanding": (
            str(summary["outstandingAmount"])
            if summary["outstandingAmount"] is not None else None
        ),
    }


class InvoiceStatusBody(BaseModel):
    status: str


@router.post("/orders/{po_id}/invoice-status")
def set_invoice_status(po_id: int, body: InvoiceStatusBody, request: Request,
                       db: Session = Depends(get_db)) -> dict[str, Any]:
    po = _po_or_404(db, po_id)
    try:
        svc.set_invoice_status(db, po, body.status, actor=current_actor(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "invoiceStatus": po.invoice_status}


@router.get("/orders/{po_id}")
def order_detail(po_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    po = _po_or_404(db, po_id)
    items = db.query(PurchaseAllocationItem).filter_by(po_id=po.id).all()
    expenses = db.query(PurchaseExtraExpense).filter_by(po_id=po.id).all()
    invoice_summary = invoice_truth.purchase_invoice_truth(db, external=po)
    invoices = [
        {
            "id": row["invoiceId"],
            "invoiceNo": row["invoiceNo"],
            "invoiceAmount": str(row["originalAmount"]) if row["originalAmount"] is not None else None,
            "allocatedAmount": str(row["allocatedAmount"]) if row["allocatedAmount"] is not None else None,
            "effectiveAllocatedAmount": str(row["amount"]),
            "invoiceDate": row["issueDate"],
            "invoiceKind": row["invoiceKind"],
        }
        for row in invoice_summary["entries"]
        if row["invoiceKind"] == "manual"
    ]
    tax_invoices = [
        {
            "id": row["invoiceId"],
            "invoiceNumber": row["invoiceNo"],
            "status": row["status"],
            "totalAmount": str(row["originalAmount"]),
            "effectiveInvoiceAmount": str(row["effectiveInvoiceAmount"]),
            "allocatedAmount": (
                str(row["allocatedAmount"]) if row["allocatedAmount"] is not None else None
            ),
            "effectiveAllocatedAmount": str(row["amount"]),
            "issueDate": row["issueDate"],
            "matchMethod": row["matchMethod"],
            "confirmed": True,
            "redAdjusted": row["redAdjusted"],
            "needsReview": row["needsReview"],
            "reviewReason": row["reviewReason"],
        }
        for row in invoice_summary["entries"]
        if row["invoiceKind"] == "tax"
    ]
    return {
        "id": po.id, "externalOrderId": po.external_order_id, "platform": po.platform,
        "supplierName": po.supplier_name, "title": po.title,
        "orderAmount": str(po.order_amount) if po.order_amount is not None else None,
        "paidAmount": str(po.paid_amount) if po.paid_amount is not None else None,
        "orderedAt": po.ordered_at.isoformat() if po.ordered_at else None,
        "purchaseStatus": po.purchase_status,
        "invoiceStatus": invoice_summary["status"],
        "invoicedAmount": str(invoice_summary["invoicedAmount"]),
        "invoiceOutstanding": (
            str(invoice_summary["outstandingAmount"])
            if invoice_summary["outstandingAmount"] is not None else None
        ),
        "invoiceNeedsReview": invoice_summary["needsReview"],
        "invoiceReviewReasons": invoice_summary["reviewReasons"],
        "allocations": [
            {"id": i.id, "skuId": i.sku_id, "skuCode": i.sku_code, "goodsName": i.goods_name,
             "quantity": str(i.quantity) if i.quantity is not None else None,
             "unitPrice": str(i.unit_price) if i.unit_price is not None else None,
             "amount": str(i.amount) if i.amount is not None else None}
            for i in items
        ],
        "expenses": [
            {"id": e.id, "expenseType": e.expense_type,
             "amount": str(e.amount) if e.amount is not None else None, "note": e.note}
            for e in expenses
        ],
        "invoices": invoices,
        "taxInvoices": tax_invoices,
        "balance": _balance(db, po),
    }


# ---------- SKU 匹配工作台 ----------

from app.services import sku_matching_service as match_svc


@router.post("/sku-matching/inbound-auto")
def run_inbound_auto_match(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """入库明细 ↔ 货品档案自动匹配；人工指定的结果不会被覆盖。"""
    stats = match_svc.match_inbound_items(db)
    audit(
        db,
        current_actor(request),
        "purchase.sku_match.auto",
        "jackyun_goods_document_items",
        detail=stats,
    )
    return {"ok": True, "stats": stats}


@router.post("/sku-matching/outbound-auto")
def run_outbound_auto_match(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """销售出库明细 ↔ 货品档案自动匹配；人工指定的结果不会被覆盖。"""
    stats = match_svc.match_inbound_items(db, include_outbound=True)
    audit(
        db,
        current_actor(request),
        "purchase.sku_match.outbound_auto",
        "jackyun_goods_document_items",
        detail=stats,
    )
    return {"ok": True, "stats": stats}


@router.get("/sku-matching/inbound")
def inbound_match_summary(db: Session = Depends(get_db)) -> dict[str, Any]:
    """入库明细匹配状态汇总 + 异常清单（missing / price_mismatch）。"""
    return match_svc.inbound_match_summary(db)


@router.get("/sku-matching/pending")
def pending_sku_allocation(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """待人工配置 SKU 的 1688 订单（无分配明细或金额不平衡），带候选与校验提示。"""
    pairs = [(source, po) for source, po in _source_pairs(db) if po is not None]
    out = []
    for source, po in pairs:
        alloc_count = db.query(PurchaseAllocationItem).filter_by(po_id=po.id).count()
        bal = match_svc.allocation_balance(db, po)
        if alloc_count > 0 and bal["balanced"]:
            continue
        file_order_id = source.id if source else None
        out.append({
            "poId": po.id,
            "fileOrderId": file_order_id,
            "externalOrderId": po.external_order_id,
            "supplierName": po.supplier_name,
            "orderedAt": po.ordered_at.isoformat() if po.ordered_at else None,
            "paidAmount": float(po.paid_amount) if po.paid_amount is not None else None,
            "allocationCount": alloc_count,
            "purchaseStatus": po.purchase_status,
            "editable": po.purchase_status == "pending_refine",
            "balance": bal,
        })
        if len(out) >= limit:
            break
    return out


@router.get("/sku-matching/{po_id}/candidates")
def allocation_candidates(po_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """为指定 1688 订单推荐候选 SKU（同供应商历史入库货品优先）。"""
    po = _po_or_404(db, po_id)
    return {"poId": po.id, "candidates": match_svc.allocation_candidates(db, po)}


class InboundManualMatchBody(BaseModel):
    sku_id: int


class CostConfirmationBody(BaseModel):
    """成本主档写入必须由调用方显式确认，避免空 POST 误改利润口径。"""

    confirm: bool = False


@router.post("/sku-matching/inbound/{item_id}/manual")
def inbound_manual_match(item_id: int, body: InboundManualMatchBody, request: Request,
                         db: Session = Depends(get_db)) -> dict[str, Any]:
    """人工指定或更换入库明细的 SKU；后续自动扫描会保留该结果。"""
    from app.models.jackyun import JackyunGoodsDocumentItem

    item = db.get(JackyunGoodsDocumentItem, item_id)
    if not item:
        raise HTTPException(404, "入库明细不存在")
    sku = db.get(ProductSku, body.sku_id)
    if not sku:
        raise HTTPException(404, "SKU 不存在")
    previous_sku_id = item.matched_sku_id
    previous_status = item.match_status
    item.matched_sku_id = sku.id
    item.match_status = "manual"
    item.match_note = f"人工指定 → {sku.sku_code} {sku.sku_name}".strip()
    audit(
        db,
        current_actor(request),
        "purchase.sku_match.manual",
        "jackyun_goods_document_items",
        item.id,
        {
            "previousSkuId": previous_sku_id,
            "previousStatus": previous_status,
            "skuId": sku.id,
            "skuCode": sku.sku_code,
        },
    )
    return {"ok": True, "itemId": item.id, "matchedSkuId": sku.id, "status": "manual"}


@router.delete("/sku-matching/inbound/{item_id}/manual")
def inbound_manual_unmatch(item_id: int, request: Request,
                           db: Session = Depends(get_db)) -> dict[str, Any]:
    """人工解除入库明细的 SKU；保留人工待处理状态，自动扫描不会重新绑定。"""
    from app.models.jackyun import JackyunGoodsDocumentItem

    item = db.get(JackyunGoodsDocumentItem, item_id)
    if not item:
        raise HTTPException(404, "入库明细不存在")
    previous_sku_id = item.matched_sku_id
    previous_status = item.match_status
    item.matched_sku_id = None
    item.match_status = "manual"
    item.match_note = "人工解除 SKU 匹配，待重新指定"
    audit(
        db,
        current_actor(request),
        "purchase.sku_match.unlinked",
        "jackyun_goods_document_items",
        item.id,
        {
            "previousSkuId": previous_sku_id,
            "previousStatus": previous_status,
        },
    )
    return {"ok": True, "itemId": item.id, "matchedSkuId": None, "status": "manual"}


@router.post("/sku-matching/inbound/{item_id}/accept-cost")
def inbound_accept_cost(item_id: int, request: Request, body: CostConfirmationBody | None = None,
                        db: Session = Depends(get_db)) -> dict[str, Any]:
    """人工确认一条异常成本；优先按关联 1688 实付比例分摊，否则用入库单价。"""
    from app.models.jackyun import JackyunGoodsDocumentItem

    if body is None or not body.confirm:
        raise HTTPException(400, "请明确确认成本变更后再提交")
    item = db.get(JackyunGoodsDocumentItem, item_id)
    if not item:
        raise HTTPException(404, "入库明细不存在")
    if item.matched_sku_id is None:
        raise HTTPException(400, "该明细未匹配 SKU，请先人工指定")
    # 1688 锚定口径：已挂 1688 订单按实付/单据比例分摊，否则按入库单价；
    # 偏离同 SKU 中位数 >50% 拒绝（疑似单据录错）。
    report = match_svc.confirm_anomaly_costs(db, only_item_id=item.id)
    if report["confirmedRows"]:
        row = report["confirmedRows"][0]
        audit(
            db,
            current_actor(request),
            "purchase.sku_cost.accept",
            "product_skus",
            row["skuId"],
            {
                "itemId": item.id,
                "documentId": item.document_id,
                "basis": row["basis"],
                "oldCost": row["oldCost"],
                "newCost": row["newCost"],
            },
        )
        return {
            "ok": True,
            "itemId": item.id,
            "skuId": row["skuId"],
            "basis": row["basis"],
            "oldCost": row["oldCost"],
            "newCost": row["newCost"],
        }
    failed_item = next((s for s in report["skipped"] if s.get("itemId") == item.id), None)
    if failed_item:
        reason = failed_item.get("reason")
        if not reason and failed_item.get("median") is not None:
            reason = (
                f"推算单价 {failed_item.get('unitCost')} 偏离同 SKU 历史中位数 "
                f"{failed_item.get('median')} 超过 50%，请先核对吉客云单据金额或数量"
            )
        raise HTTPException(400, reason or "无法推算成本，请核对该明细金额与数量")
    raise HTTPException(400, "无法推算成本，请核对该明细金额与数量")


class BatchAcceptCostBody(BaseModel):
    document_id: int
    confirm: bool = False


@router.post("/sku-matching/inbound/batch-accept-cost")
def inbound_batch_accept_cost(body: BatchAcceptCostBody, request: Request,
                              db: Session = Depends(get_db)) -> dict[str, Any]:
    """人工按单确认异常成本；优先按关联 1688 实付比例分摊，否则用入库单价。"""
    from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem

    if not body.confirm:
        raise HTTPException(400, "请明确确认成本变更后再提交")
    doc = db.get(JackyunGoodsDocument, body.document_id)
    if not doc:
        raise HTTPException(404, "入库单不存在")
    items = (
        db.query(JackyunGoodsDocumentItem)
        .filter(
            JackyunGoodsDocumentItem.document_id == body.document_id,
            JackyunGoodsDocumentItem.match_status == "price_mismatch",
            JackyunGoodsDocumentItem.matched_sku_id.isnot(None),
        )
        .all()
    )
    if not items:
        return {"ok": True, "accepted": 0, "failed": []}
    # 1688 锚定口径批量确认 + 中位数守门（偏离 >50% 跳过并标注）
    report = match_svc.confirm_anomaly_costs(db, document_id=body.document_id)
    for row in report["confirmedRows"]:
        audit(
            db,
            current_actor(request),
            "purchase.sku_cost.accept",
            "product_skus",
            row["skuId"],
            {
                "itemId": row["itemId"],
                "documentId": body.document_id,
                "basis": row["basis"],
                "oldCost": row["oldCost"],
                "newCost": row["newCost"],
            },
        )
    failed = [
        {
            "itemId": s["itemId"],
            "reason": s.get(
                "reason",
                f"推算单价 {s.get('unitCost')} 偏离同 SKU 历史中位数 {s.get('median')} 超过 50%，请先核对吉客云单据金额或数量",
            ),
        }
        for s in report["skipped"]
    ]
    return {"ok": True, "documentId": body.document_id, "accepted": report["confirmed"], "failed": failed}


@router.delete("/orders/{po_id}/jackyun-link/{jackyun_po_id}")
def jackyun_unlink(po_id: int, jackyun_po_id: int, request: Request,
                   db: Session = Depends(get_db)) -> dict[str, Any]:
    """按吉客云采购单 ID 解除关联；状态回退规则与 link_id 入口完全一致。"""
    po = _po_or_404(db, po_id)
    link = (
        db.query(JackyunPurchaseOrderLink)
        .filter_by(po_id=po.id, jackyun_po_id=jackyun_po_id)
        .first()
    )
    if not link:
        raise HTTPException(404, "该订单未关联此采购单")
    try:
        return svc.unlink_jackyun_po_link(db, po, link, actor=current_actor(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
