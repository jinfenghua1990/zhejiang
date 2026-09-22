"""受登录保护的吉客云客户端导出文件上传入口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.db import get_db
from app.services import jackyun_file_import_service as service
from app.services.import_lifecycle import LifecycleTransitionError
from app.utils.uploads import UploadTooLargeError, read_upload_limited

router = APIRouter(prefix="/jackyun-files", tags=["jackyun-files"])


@router.post("/imports")
async def upload_import(
    request: Request,
    file: UploadFile = File(...),
    auto_confirm: bool = Query(True, description="上传后立即确认并执行采购自动化"),
    db: Session = Depends(get_db),
) -> dict:
    """上传官方客户端导出的 XLSX/CSV，默认进入 draft 暂存。"""
    try:
        content = await read_upload_limited(file, max_bytes=service.settings.MAX_JACKYUN_IMPORT_BYTES)
        row, duplicate = service.import_export(
            db,
            content=content,
            original_name=file.filename or "jackyun-export.xlsx",
            actor=current_actor(request),
            auto_confirm=auto_confirm,
        )
    except UploadTooLargeError as exc:
        raise HTTPException(413, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    automation = None
    map_result = None
    if auto_confirm:
        try:
            map_result = service.map_import(db, row.id, actor=current_actor(request))
        except Exception as exc:
            map_result = {"ok": False, "error": str(exc)}
        from app.services.procurement_chain_service import run_full_procurement_automation
        automation = run_full_procurement_automation(db, actor=current_actor(request))
    return {
        "duplicate": duplicate,
        "lifecycle": row.lifecycle,
        "import": service.serialize_import(row),
        "mapResult": map_result,
        "automation": automation,
    }


@router.get("/imports")
def list_imports(
    limit: int = Query(50, ge=1, le=200),
    lifecycle: str | None = Query(None, pattern="^(draft|active|deleted)$"),
    db: Session = Depends(get_db),
) -> list[dict]:
    return service.list_imports(db, lifecycle=lifecycle, limit=limit)


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
    # 确认即映射：由分发器按表头选择映射器（采购单 / 入库申请单货品 / 无匹配则存档）。
    try:
        result["mapResult"] = service.map_import(db, import_id, actor=current_actor(request))
    except Exception as exc:  # 映射失败不阻断确认，错误带回面板
        result["mapResult"] = {"ok": False, "error": str(exc)}
    # 兼容旧前端字段
    if result["mapResult"].get("mapper") == "purchase":
        result["mapPurchase"] = result["mapResult"]
    from app.services.procurement_chain_service import run_full_procurement_automation
    result["automation"] = run_full_procurement_automation(db, actor=current_actor(request))
    return result


@router.delete("/imports/{import_id}", status_code=204)
def soft_delete_import(
    request: Request,
    import_id: int,
    db: Session = Depends(get_db),
) -> None:
    try:
        service.soft_delete_import(db, import_id, actor=current_actor(request))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except LifecycleTransitionError as exc:
        raise HTTPException(409, str(exc))


@router.post("/imports/{import_id}/restore")
def restore_import(
    request: Request,
    import_id: int,
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = service.restore_import(db, import_id, actor=current_actor(request))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except LifecycleTransitionError as exc:
        raise HTTPException(409, str(exc))
    return service.serialize_import(row)


@router.get("/imports/{import_id}/records")
def get_import_records(
    import_id: int,
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[dict]:
    return service.list_records(db, import_id, limit=limit)


@router.delete("/imports/{import_id}/records/{row_index}", status_code=204)
def delete_import_row(
    request: Request,
    import_id: int,
    row_index: int,
    db: Session = Depends(get_db),
) -> None:
    """明细核对：删除单行原始记录（可恢复）。"""
    try:
        service.delete_row(db, import_id, row_index, actor=current_actor(request))
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.post("/imports/{import_id}/records/{row_index}/restore")
def restore_import_row(
    request: Request,
    import_id: int,
    row_index: int,
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = service.restore_row(db, import_id, row_index, actor=current_actor(request))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return {"rowIndex": row.row_index, "rowStatus": row.row_status}

@router.post("/imports/{import_id}/map-purchase")
def map_purchase(
    request: Request,
    import_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """把已确认的采购报表映射为 JackyunPurchaseOrder（幂等，可重复触发）。"""
    try:
        result = service.map_purchase_import(db, import_id, actor=current_actor(request))
        from app.services.procurement_chain_service import run_full_procurement_automation

        result["automation"] = run_full_procurement_automation(
            db,
            actor=current_actor(request),
        )
        return result
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/imports/{import_id}/map-outbound")
def map_outbound(
    request: Request,
    import_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """把已确认的销售出库报表映射为吉客云出库单及货品明细。"""
    try:
        result = service.map_outbound_documents(db, import_id, actor=current_actor(request))
        from app.services.partner_master_service import rebuild_partner_master

        result["partnerMaster"] = rebuild_partner_master(
            db,
            actor=current_actor(request),
            run_payment_match=False,
        )
        db.commit()
        return result
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/purchase-orders")
def list_purchase_orders(
    q: str = Query("", description="按采购单号/供应商名过滤"),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[dict]:
    """已映射的吉客云采购单列表（供订单详情匹配候选）。"""
    from app.models.purchase import JackyunPurchaseOrder

    query = db.query(JackyunPurchaseOrder).order_by(JackyunPurchaseOrder.id.desc())
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                JackyunPurchaseOrder.jackyun_purch_id.ilike(like),
                JackyunPurchaseOrder.purch_no.ilike(like),
                JackyunPurchaseOrder.supplier_name.ilike(like),
            )
        )
    return [service._serialize_jpo(row) for row in query.limit(limit).all()]


@router.post("/imports/{import_id}/map")
def map_import(
    request: Request,
    import_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """按报表内容自动映射（幂等）：采购单 / 入库申请单货品 / 无匹配则仅存档。"""
    try:
        return service.map_import(db, import_id, actor=current_actor(request))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
