"""采购全链路 API：1688 采购 → 入库 → 发票 → 付款 → 税务认证。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.core.audit import audit
from app.db import get_db
from app.services import procurement_chain_service as service

router = APIRouter(prefix="/procurement-chain", tags=["采购全链路"])


@router.get("/workspace")
def chain_workspace(
    limit: int = Query(500, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """当前链路工作台聚合入口：一次返回漏斗、订单和待确认建议。"""
    return service.workspace(db, limit=limit, offset=offset)


@router.get("/overview")
def chain_overview(db: Session = Depends(get_db)) -> dict:
    """五环节漏斗统计。"""
    return service.overview(db)


@router.get("/orders")
def chain_orders(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """1688 订单链路列表（每行一单）。"""
    return service.list_chain(db, limit=limit, offset=offset)


@router.get("/records")
def chain_records(
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """平铺全量记录：全部 1688 订单 + 吉客云入库/结算 + 税务发票，每行标记数据维度。"""
    return service.list_all_records(db, limit=limit, offset=offset)


@router.get("/orders/{order_id}")
def chain_order_detail(order_id: int, db: Session = Depends(get_db)) -> dict:
    """单个订单链路详情（二级页面）：五环节明细 + 本单待确认建议 + 供应商历史。"""
    detail = service.get_order_detail(db, order_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    supplier = detail.get("supplier") or ""
    if supplier:
        detail["supplierHistory"] = service.order_supplier_history(
            db, supplier, exclude_order_id=detail.get("orderId")
        )
    return detail


@router.get("/execution-overview")
def execution_overview(db: Session = Depends(get_db)) -> dict:
    """采购执行中心顶部统计卡：待处理 / 待确认 / 待匹配 / 待生成采购单 / 待发票。"""
    return service.execution_overview(db)


@router.get("/suppliers")
def suppliers(
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """供应商聚合列表（管理视角）。"""
    return service.supplier_summaries(db, limit=limit, offset=offset)


@router.get("/suppliers/{supplier_name}")
def supplier_detail(supplier_name: str, db: Session = Depends(get_db)) -> dict:
    """单个供应商详情：历史合作 + 常购 SKU + 最近订单。"""
    detail = service.supplier_detail(db, supplier_name)
    if detail is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    return detail


@router.get("/pending")
def chain_pending(db: Session = Depends(get_db)) -> dict:
    """待确认的关联建议列表。"""
    return service.list_pending(db)


@router.get("/candidates")
def link_candidates(
    order_id: int = Query(...),
    target_type: str = Query(...),
    q: str = Query("", max_length=200),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    """列出入库单/结算单，供用户在订单详情中手工选择或更换。"""
    try:
        return service.list_link_candidates(db, order_id, target_type, q=q, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/orders/{order_id}/auto-link-settlement")
def auto_link_settlement(order_id: int, db: Session = Depends(get_db)) -> dict:
    """为单个 1688 订单自动匹配并关联结算单（供应商+金额+时间全吻合且候选唯一才关联）。"""
    matcher = service.ProcurementChainMatcher(db)
    try:
        return matcher.auto_link_settlement(order_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/run-match")
def run_match(
    auto_confirm: bool = Query(True, description="默认自动确认；传 false 只生成候选"),
    db: Session = Depends(get_db),
) -> dict:
    """运行采购自动化；默认匹配、建链并确认。"""
    if auto_confirm:
        result = service.run_full_procurement_automation(db, actor="system")
        match = result.get("match") or {}
        return {
            **result,
            "created": int(match.get("created", 0)),
            "skipped": int(match.get("skipped", 0)),
            "requiresConfirmation": bool(match.get("requiresConfirmation", False)),
        }
    return service.ProcurementChainMatcher(db).run_match(auto_confirm=False)


@router.post("/auto-confirm")
def auto_confirm_all(request: Request, db: Session = Depends(get_db)) -> dict:
    """确认当前所有可验证的订单、入库、结算和发票关联。"""
    return service.auto_confirm_pending_links(db, actor=current_actor(request))


class XrefBody(BaseModel):
    content: str
    persist_to_file: bool = True


class ConsumableUsageItemBody(BaseModel):
    consumable_id: int
    quantity: str


class ConsumableUsageBody(BaseModel):
    enabled: bool
    items: list[ConsumableUsageItemBody] = []
    note: str = ""


class LinkBody(BaseModel):
    order_id: int
    target_type: str
    target_id: int
    note: str = ""
    consumable_usage_enabled: bool | None = None
    consumable_usage_items: list[ConsumableUsageItemBody] = []


class ReplaceLinkBody(BaseModel):
    target_id: int
    note: str = ""
    consumable_usage_enabled: bool | None = None
    consumable_usage_items: list[ConsumableUsageItemBody] = []


class DocumentAmountBody(BaseModel):
    amount: str
    note: str = ""


class DocumentDateBody(BaseModel):
    inbound_at: datetime
    note: str = ""


class DocumentItemPriceBody(BaseModel):
    unit_price_tax: str
    note: str = ""


class LocalInboundItemBody(BaseModel):
    allocation_id: int
    quantity: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]
    unit_price: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4)] | None = None


class LocalInboundBody(BaseModel):
    order_id: int
    inbound_no: str = Field(default="", max_length=128)
    inbound_at: datetime | None = None
    warehouse_id: int | None = None
    note: str = ""
    items: list[LocalInboundItemBody] = Field(min_length=1, max_length=500)
    consumable_usage_enabled: bool | None = None
    consumable_usage_items: list[ConsumableUsageItemBody] = []


@router.post("/local-inbounds")
def create_local_inbound(body: LocalInboundBody, request: Request, db: Session = Depends(get_db)) -> dict:
    """由本系统创建采购入库主单，不调用吉客云接口。"""
    from app.services import local_inbound_service

    try:
        document, link = local_inbound_service.create_purchase_inbound(
            db,
            order_id=body.order_id,
            inbound_no=body.inbound_no,
            inbound_at=body.inbound_at,
            warehouse_id=body.warehouse_id,
            items=[item.model_dump() for item in body.items],
            note=body.note,
            actor=current_actor(request),
        )
        # 兼容旧客户端仍携带的耗材字段，但采购入库统一由实际入库明细和
        # 正品↔耗材映射自动计算；人工修正请使用独立的历史修正接口。
        from app.services.consumable_service import auto_apply_inbound_usage
        usage = auto_apply_inbound_usage(db, link.id, note="本系统采购入库按 SKU 耗材映射处理")
    except (TypeError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))

    audit(db, current_actor(request), "purchase.local_inbound.create", "jackyun_goods_documents", document.id, {
        "inboundNo": document.goodsdoc_no,
        "platformPurchaseOrderNo": (document.raw or {}).get("platformPurchaseOrderNo"),
        "linkId": link.id,
    })
    return {
        "ok": True,
        "documentId": document.id,
        "inboundNo": document.goodsdoc_no,
        "linkId": link.id,
        "source": "local_purchase_inbound",
        "usage": usage,
    }


@router.delete("/inbound-documents/{document_id}")
def delete_local_inbound(document_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    """删除本系统采购入库；吉客云历史入库单不允许删除。"""
    from app.services import local_inbound_service

    try:
        result = local_inbound_service.delete_local_purchase_inbound(db, document_id=document_id)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    audit(db, current_actor(request), "purchase.local_inbound.delete", "jackyun_goods_documents", document_id, {
        "inboundNo": result["inboundNo"],
        "removedLinkIds": result["removedLinkIds"],
    })
    return result


@router.patch("/inbound-documents/{document_id}/amount")
def patch_inbound_document_amount(request: Request, document_id: int, body: DocumentAmountBody,
                                  db: Session = Depends(get_db)) -> dict:
    """人工更正吉客云入库单金额（录错 head 时）；入库单本身不被外部删除，仅修正金额口径。"""
    from app.models.jackyun import JackyunGoodsDocument
    from app.services import inbound_allocation_seed as seed_svc
    from app.core.audit import audit

    doc = db.get(JackyunGoodsDocument, document_id)
    if doc is None:
        raise HTTPException(404, "入库单不存在")
    result = seed_svc.set_document_amount(db, document_id, body.amount, actor=current_actor(request))
    if not result["ok"]:
        raise HTTPException(400, result["reason"])
    audit(db, current_actor(request), "purchase.inbound.amount_correct",
          "jackyun_goods_documents", document_id,
          {"after": result["after"], "note": body.note})
    return {"ok": True, "documentId": document_id, "amount": result["after"]}


@router.post("/inbound-documents/{document_id}/recalc-amount")
def recalc_inbound_document_amount(request: Request, document_id: int,
                                   db: Session = Depends(get_db)) -> dict:
    """按明细合计重算入库单金额（head=Σ amount_tax），修正单据头与明细不一致。"""
    from app.services import inbound_allocation_seed as seed_svc
    from app.core.audit import audit

    result = seed_svc.recalc_document_amount(db, document_id, actor=current_actor(request))
    if not result["ok"]:
        raise HTTPException(404, result["reason"])
    audit(db, current_actor(request), "purchase.inbound.recalc_amount",
          "jackyun_goods_documents", document_id,
          {"before": result["before"], "after": result["after"]})
    return {"ok": True, "before": result["before"], "after": result["after"]}


@router.patch("/inbound-documents/{document_id}/date")
def patch_inbound_document_date(request: Request, document_id: int, body: DocumentDateBody,
                                db: Session = Depends(get_db)) -> dict:
    """人工更正入库单日期（日期决定成本落入哪个报告期，录错会让历史月份取不到成本）。"""
    from app.services import inbound_edit_service

    try:
        result = inbound_edit_service.set_document_date(db, document_id, body.inbound_at)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    audit(db, current_actor(request), "purchase.inbound.date_correct",
          "jackyun_goods_documents", document_id,
          {"before": result["before"], "after": result["after"], "note": body.note})
    return result


@router.patch("/inbound-documents/{document_id}/items/{item_id}/price")
def patch_inbound_item_price(request: Request, document_id: int, item_id: int,
                             body: DocumentItemPriceBody, db: Session = Depends(get_db)) -> dict:
    """人工更正入库明细含税单价，并联动重算该行金额与单据头金额。"""
    from app.services import inbound_edit_service

    try:
        result = inbound_edit_service.set_item_unit_price(db, document_id, item_id, body.unit_price_tax)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    audit(db, current_actor(request), "purchase.inbound.item_price_correct",
          "jackyun_goods_document_items", item_id,
          {key: result[key] for key in (
              "documentId", "lineNo", "before", "after",
              "amountTaxBefore", "amountTaxAfter",
              "documentAmountBefore", "documentAmountAfter",
          )} | {"note": body.note})
    return result


@router.post("/xref/preview")
def xref_preview(body: XrefBody, db: Session = Depends(get_db)) -> dict:
    """解析粘贴/上传的 1688↔RK 对照表文本（TSV），返回将创建/跳过/缺失明细；不写库。"""
    from app.services.procurement_chain_xref import preview_xref_links

    preview = preview_xref_links(db, content_or_path=body.content)
    return {
        "totalXrefRows": preview["total_xref_rows"],
        "totalPairs": preview["total_pairs"],
        "stats": preview["stats"],
        "toCreate": [
            {k: v for k, v in item.items() if k in {"order_no", "rk_no", "barcodes", "products", "qty_boxes", "note_extra"}}
            for item in preview["to_create"][:50]
        ],
        "missingOrder": [p["order_no"] for p in preview["missing_order"]],
        "missingRk": [p["rk_no"] for p in preview["missing_rk"]],
        "noiseLinkCount": preview["stats"]["existing_noise"],
    }


@router.post("/xref/apply")
def xref_apply(body: XrefBody, db: Session = Depends(get_db)) -> dict:
    """应用 1688↔RK 对照表：写库为已确认链并落盘到 DATA_DIR。"""
    from app.services.procurement_chain_xref import apply_xref_links

    return apply_xref_links(db, actor="agent", content_or_path=body.content,
                            persist_to_file=body.persist_to_file)


@router.get("/xref/file")
def xref_file() -> dict:
    """返回当前对照表原始文本与最近修改时间，供工作台编辑面板回填。"""
    from app.services.procurement_chain_xref import XREF_PATH

    if XREF_PATH.exists():
        return {
            "exists": True,
            "content": XREF_PATH.read_text(encoding="utf-8"),
            "modifiedAt": XREF_PATH.stat().st_mtime,
            "size": XREF_PATH.stat().st_size,
        }
    return {"exists": False, "content": "", "modifiedAt": None, "size": 0}


@router.post("/prelink")
def prelink(
    auto: bool = Body(True, description="True=高置信自动确认建链；False=只生成待确认建议（干跑预览）"),
    db: Session = Depends(get_db),
) -> dict:
    """多因子入库预关联：供应商 + 时间窗 + SKU 货品重合率，支持 1:N 分批到货。

    - 默认所有达到匹配阈值的候选自动建链 confirmed=True
    - 传 auto=false 时只生成 pending 建议，供人工核对
    - 金额仅作弱加分，避免预付/抵扣导致的金额瞎配
    """
    from app.services.procurement_prelink_service import prelink_inbound

    return prelink_inbound(db, actor="agent", auto=auto)


@router.post("/links")
def manual_link(body: LinkBody, db: Session = Depends(get_db)) -> dict:
    """人工创建 1688 订单 ↔ 入库单/结算单 关联。"""
    if body.target_type not in ("inbound", "settlement"):
        raise HTTPException(status_code=400, detail="target_type 只能是 inbound/settlement")
    matcher = service.ProcurementChainMatcher(db)
    usage_result = None
    try:
        link = matcher.manual_link(body.order_id, body.target_type, body.target_id, note=body.note)
        if body.target_type == "inbound":
            if body.consumable_usage_enabled is None:
                from app.services.consumable_service import auto_apply_inbound_usage
                usage_result = auto_apply_inbound_usage(db, link.id, note="采购入库自动按 SKU 耗材映射关联")
            else:
                from app.services.consumable_service import set_inbound_usage
                set_inbound_usage(
                    db, link_id=link.id, enabled=body.consumable_usage_enabled,
                    items=[item.model_dump() for item in body.consumable_usage_items], note=body.note,
                )
    except ValueError as exc:
        if 'link' in locals() and link is not None and body.target_type == "inbound":
            matcher.remove_link(link.id)
        raise HTTPException(status_code=400, detail=str(exc))
    return {"id": link.id, "confirmed": True, "autoUsage": usage_result}


@router.put("/links/{link_id}")
def replace_link(link_id: int, body: ReplaceLinkBody, db: Session = Depends(get_db)) -> dict:
    """把一条已有关联原子更换到另一张业务单据。"""
    target_id = body.target_id
    matcher = service.ProcurementChainMatcher(db)
    usage_result = None
    try:
        link = matcher.replace_link(link_id, target_id, note=body.note)
        if link.target_type == "inbound":
            from app.services.consumable_service import auto_apply_inbound_usage
            usage_result = auto_apply_inbound_usage(db, link.id, note="采购入库自动按 SKU 耗材映射关联")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"id": link.id, "confirmed": True, "targetId": link.target_id, "autoUsage": usage_result}


@router.post("/links/{link_id}/consumable-usage")
def set_link_consumable_usage(link_id: int, body: ConsumableUsageBody, db: Session = Depends(get_db)) -> dict:
    """历史入库关联的耗材流水修正接口；正常流程由系统自动计算。"""
    from app.services.consumable_service import set_inbound_usage

    try:
        items = [item.model_dump() for item in body.items]
        usage = set_inbound_usage(db, link_id=link_id, enabled=body.enabled, items=items, note=body.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"linkId": link_id, "decided": True, "enabled": body.enabled, "items": usage}


@router.post("/links/{link_id}/confirm")
def confirm_link(link_id: int, body: ConsumableUsageBody | None = Body(None), db: Session = Depends(get_db)) -> dict:
    """确认一条待确认关联；入库耗材由系统按实际入库数量自动计算。"""
    matcher = service.ProcurementChainMatcher(db)
    usage_result = None
    try:
        link = matcher.confirm(link_id)
        if link.target_type == "inbound":
            from app.services.consumable_service import auto_apply_inbound_usage
            usage_result = auto_apply_inbound_usage(db, link_id, note="采购入库自动按 SKU 耗材映射关联")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"id": link.id, "confirmed": True, "autoUsage": usage_result}


@router.delete("/links/{link_id}")
def delete_link(link_id: int, db: Session = Depends(get_db)) -> dict:
    matcher = service.ProcurementChainMatcher(db)
    matcher.remove_link(link_id)
    return {"ok": True}


@router.post("/invoice-links/{link_id}/confirm")
def confirm_invoice_link(link_id: int, db: Session = Depends(get_db)) -> dict:
    matcher = service.ProcurementChainMatcher(db)
    try:
        link = matcher.confirm_invoice(link_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"id": link.id, "confirmed": True}


@router.post("/invoice-links")
def manual_invoice_link(order_id: int = Body(...), invoice_id: int = Body(...),
                        note: str = Body(""), db: Session = Depends(get_db)) -> dict:
    try:
        link = service.ProcurementChainMatcher(db).manual_invoice_link(order_id, invoice_id, note)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": link.id, "confirmed": True}


@router.delete("/invoice-links/{link_id}")
def delete_invoice_link(link_id: int, db: Session = Depends(get_db)) -> dict:
    matcher = service.ProcurementChainMatcher(db)
    matcher.remove_invoice_link(link_id)
    return {"ok": True}


@router.post("/invoices/{invoice_id}/verify")
def verify_invoice(
    invoice_id: int,
    verified: bool = Body(...),
    verified_month: str = Body(""),
    db: Session = Depends(get_db),
) -> dict:
    """标记发票是否已勾选认证（verified_month 如 2026-08）。"""
    matcher = service.ProcurementChainMatcher(db)
    try:
        inv = matcher.set_invoice_verified(invoice_id, verified, verified_month)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"invoiceId": inv.id, "verified": inv.verified, "verifiedMonth": inv.verified_month}
