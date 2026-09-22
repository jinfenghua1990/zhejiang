"""税务系统官方发票清单导入与台账查询。"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.db import get_db
from app.services import tax_invoice_service as service
from app.services.import_lifecycle import LifecycleTransitionError
from app.utils.uploads import UploadTooLargeError, read_upload_limited

router = APIRouter(prefix="/tax-invoices", tags=["tax-invoices"])


@router.post("/imports")
async def upload_import(
    request: Request,
    file: UploadFile = File(...),
    period_year: int = Query(0, ge=0, le=9999),
    period_month: int = Query(0, ge=0, le=12),
    auto_confirm: bool = Query(True, description="上传后立即确认并执行采购自动化"),
    db: Session = Depends(get_db),
) -> dict:
    """上传税务系统官方导出的 XLSX/CSV；默认进入 draft 暂存。"""
    try:
        content = await read_upload_limited(file, max_bytes=service.settings.MAX_UPLOAD_BYTES)
        row, duplicate = service.import_export(
            db,
            content=content,
            original_name=file.filename or "tax-invoices.xlsx",
            actor=current_actor(request),
            period_year=period_year,
            period_month=period_month,
            auto_confirm=auto_confirm,
        )
    except UploadTooLargeError as exc:
        raise HTTPException(413, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    automation = None
    if auto_confirm:
        from app.services.procurement_chain_service import run_full_procurement_automation
        automation = run_full_procurement_automation(db, actor=current_actor(request))
    return {
        "duplicate": duplicate,
        "lifecycle": row.lifecycle,
        "import": service.serialize_import(row),
        "automation": automation,
    }


@router.get("/imports")
def list_imports(
    limit: int = Query(50, ge=1, le=200),
    lifecycle: str | None = Query(None, pattern="^(draft|active|deleted)$"),
    db: Session = Depends(get_db),
) -> list[dict]:
    return service.list_imports(db, lifecycle=lifecycle, limit=limit)


@router.get("/imports/{import_id}/records")
def get_import_records(
    import_id: int,
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[dict]:
    return service.list_import_records(db, import_id, limit=limit)


@router.delete("/imports/{import_id}/records/{row_index}")
def delete_import_row(import_id: int, row_index: int) -> None:
    """税务原始明细属于审计底稿，禁止删除；错误数据只能标异常/重新导入纠正。"""
    raise HTTPException(
        status_code=409,
        detail="税务原始明细不可删除。请保留原始记录，通过异常标记、备注或重新导入正确文件进行纠正。",
    )


@router.post("/imports/{import_id}/records/{row_index}/restore")
def restore_import_row(
    request: Request,
    import_id: int,
    row_index: int,
    db: Session = Depends(get_db),
) -> dict:
    """仅用于恢复历史版本中曾被软删除的税务明细；新版本已禁止继续删除。"""
    try:
        row = service.restore_row(db, import_id, row_index, actor=current_actor(request))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return {"rowIndex": row.row_index, "rowStatus": row.row_status}


@router.post("/imports/{import_id}/confirm")
def confirm_import(
    request: Request,
    import_id: int,
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = service.confirm_import(db, import_id, actor=current_actor(request))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except LifecycleTransitionError as exc:
        raise HTTPException(409, str(exc))
    result = service.serialize_import(row)
    from app.services.procurement_chain_service import run_full_procurement_automation
    result["automation"] = run_full_procurement_automation(db, actor=current_actor(request))
    return result


@router.post("/imports/{import_id}/reprocess")
def reprocess_import(
    request: Request,
    import_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """用最新规则重处理已保存批次，并继续执行采购自动化。"""
    try:
        row = service.reprocess_import(db, import_id, actor=current_actor(request))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    from app.services.procurement_chain_service import run_full_procurement_automation
    result = service.serialize_import(row)
    result["automation"] = run_full_procurement_automation(db, actor=current_actor(request))
    return result


@router.delete("/imports/{import_id}")
def soft_delete_import(import_id: int) -> None:
    """官方税务导入批次作为审计资料永久保留，禁止删除整个批次。"""
    raise HTTPException(
        status_code=409,
        detail="官方税务导入批次不可删除。若导入错误，请保留原批次并重新导入正确文件，系统以有效批次和差异核对处理。",
    )


@router.post("/imports/{import_id}/restore")
def restore_import(
    request: Request,
    import_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """仅用于恢复历史版本中曾被软删除的官方税务批次。"""
    try:
        row = service.restore_import(db, import_id, actor=current_actor(request))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except LifecycleTransitionError as exc:
        raise HTTPException(409, str(exc))
    return service.serialize_import(row)


@router.get("/summary")
def get_summary(db: Session = Depends(get_db)) -> dict:
    return service.summary(db)


CATEGORY_V2_PATTERN = r"^(goods|platform_fee|operating_other|reimburse_advance|reimburse_operating|excluded|buyer_sales|platform_service|)$"


@router.get("")
def get_invoices(
    direction: str | None = Query(None, pattern="^(input|output|unknown)$"),
    status: str | None = Query(None, pattern="^(issued|void|red|unknown)$"),
    match_status: str | None = Query(None, pattern="^(matched|partial|unmatched|needs_review)$"),
    processing_status: str | None = Query(None, pattern="^(pending|required|not_required)$"),
    category: str | None = Query(None, pattern=CATEGORY_V2_PATTERN, description="v2 分类（空=待判断）"),
    verified: bool | None = Query(None, description="true=仅已认证 / false=仅未认证"),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[dict]:
    try:
        return service.list_invoices(
            db, direction=direction, status=status, match_status=match_status,
            processing_status=processing_status, category=category, verified=verified,
            limit=limit, offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class ProcessingStatusBody(BaseModel):
    processing_status: str


@router.patch("/{invoice_id}/processing-status")
def update_processing_status(
    invoice_id: int,
    body: ProcessingStatusBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """保留发票和明细，只更新进项发票的业务处理结论。"""
    try:
        invoice = service.set_processing_status(
            db, invoice_id, body.processing_status, actor=current_actor(request)
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return service.serialize_invoice(invoice, db=db)


class BulkVerifyBody(BaseModel):
    invoice_ids: list[int]
    verified: bool
    verified_month: str = ""


@router.post("/bulk-verify")
def bulk_verify_invoices(
    body: BulkVerifyBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """批量标记进项发票勾选认证状态（如税局勾选清单已核对后回填）。

    按发票主键批量调用与单张相同的核验逻辑，幂等；verified_month 形如 2026-08。
    """
    if not body.invoice_ids:
        return {"ok": True, "processed": 0, "items": []}
    items: list[dict] = []
    actor = current_actor(request)
    for invoice_id in body.invoice_ids:
        try:
            inv = service.set_invoice_verified(
                db, invoice_id, body.verified, body.verified_month, actor=actor
            )
            items.append({"invoiceId": inv.id, "verified": inv.verified, "verifiedMonth": inv.verified_month or ""})
        except (LookupError, ValueError) as exc:
            items.append({"invoiceId": invoice_id, "error": str(exc)})
    return {"ok": True, "processed": len(items), "items": items}


class BulkCategoryBody(BaseModel):
    invoice_ids: list[int]
    category: str


@router.post("/bulk-category")
def bulk_set_category(
    body: BulkCategoryBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """批量把选中的发票移动到指定分类（进项 6+1；销项 buyer_sales/platform_service；空=待判断）。"""
    try:
        updated = service.set_invoice_categories(
            db, body.invoice_ids, body.category, actor=current_actor(request)
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "processed": len(updated)}


class BulkPaymentMethodBody(BaseModel):
    invoice_ids: list[int]
    payment_method: str


@router.post("/bulk-payment-method")
def bulk_set_payment_method(
    body: BulkPaymentMethodBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """人工维护进项发票付款方式：personal=个人垫付；platform_auto_debit=平台自动扣款货款；空=清除。
    对公付款必须由已确认银行付款关联生成，销项发票不适用。"""
    try:
        updated = service.set_invoice_payment_methods(
            db, body.invoice_ids, body.payment_method, actor=current_actor(request)
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "processed": len(updated)}



class RedBlueRelationBody(BaseModel):
    blue_invoice_id: int
    note: str = ""


@router.get("/{invoice_id}/red-blue-candidates")
def get_red_blue_candidates(
    invoice_id: int,
    keyword: str = Query("", max_length=128),
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[dict]:
    try:
        return service.red_blue_candidates(db, invoice_id, keyword=keyword, limit=limit)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{invoice_id}/red-blue-link")
def set_red_blue_link(
    invoice_id: int,
    body: RedBlueRelationBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    try:
        return service.set_manual_red_blue_relation(
            db, invoice_id, body.blue_invoice_id, note=body.note, actor=current_actor(request)
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/{invoice_id}/red-blue-link")
def clear_red_blue_link(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    try:
        return service.clear_manual_red_blue_relation(db, invoice_id, actor=current_actor(request))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


class RedSettlementBody(BaseModel):
    settlement_type: str
    amount: Decimal
    target_id: int | None = None
    note: str = ""


@router.get("/{invoice_id}/refund-candidates")
def get_refund_candidates(
    invoice_id: int,
    keyword: str = Query("", max_length=128),
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[dict]:
    try:
        return service.red_refund_candidates(db, invoice_id, keyword=keyword, limit=limit)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{invoice_id}/red-settlements")
def create_red_settlement(
    invoice_id: int,
    body: RedSettlementBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    try:
        return service.add_red_settlement(
            db,
            invoice_id,
            body.settlement_type,
            body.amount,
            target_id=body.target_id,
            note=body.note,
            actor=current_actor(request),
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/{invoice_id}/red-settlements/{link_id}")
def delete_red_settlement(
    invoice_id: int,
    link_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    try:
        return service.remove_red_settlement(
            db, invoice_id, link_id, actor=current_actor(request)
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc



class VatReviewBody(BaseModel):
    vat_deductible_status: str | None = None
    input_vat_transfer_status: str | None = None
    input_vat_transfer_amount: Decimal | None = None
    note: str = ""


@router.patch("/{invoice_id}/vat-review")
def update_vat_review(
    invoice_id: int,
    body: VatReviewBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """人工确认进项抵扣/红冲进项税转出事实；每次修改均保留审计。"""
    try:
        return service.set_vat_review(
            db,
            invoice_id,
            vat_deductible_status=body.vat_deductible_status,
            input_vat_transfer_status=body.input_vat_transfer_status,
            input_vat_transfer_amount=body.input_vat_transfer_amount,
            note=body.note,
            actor=current_actor(request),
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{invoice_id}/lines")
def get_invoice_lines(
    invoice_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """一张发票的货物明细行（来自官方清单导入记录，一票多行）。"""
    try:
        return service.invoice_detail_lines(db, invoice_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{invoice_id}/purchase-link-candidates")
def get_purchase_link_candidates(
    invoice_id: int,
    keyword: str = Query("", max_length=128),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[dict]:
    """人工关联候选：销项发票按单号/买家搜销售订单；采购类发票按单号/供应商搜 1688 订单与吉客云采购单。"""
    try:
        return service.purchase_link_candidates(db, invoice_id, keyword=keyword, limit=limit)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


class PurchaseLinkBody(BaseModel):
    target_type: str
    target_id: int
    allocated_amount: Decimal | None = None
    note: str = ""


@router.post("/{invoice_id}/purchase-links")
def create_purchase_link(
    invoice_id: int,
    body: PurchaseLinkBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """人工关联业务单据（销项→销售订单；采购类发票→采购单；软删行复活，同对幂等）。"""
    try:
        return service.link_purchase_order(
            db, invoice_id, body.target_type, body.target_id,
            allocated_amount=body.allocated_amount, note=body.note, actor=current_actor(request),
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/{invoice_id}/purchase-links/{link_id}")
def delete_purchase_link(
    invoice_id: int,
    link_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """解除采购单/销售订单关联（软删可审计；同对可再次关联）。"""
    try:
        return service.unlink_purchase(db, link_id, actor=current_actor(request))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
