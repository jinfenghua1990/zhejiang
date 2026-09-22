"""1688 官方订单导出文件上传与查询 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.db import get_db
from app.services import alibaba1688_import_service as service
from app.services.import_lifecycle import LifecycleTransitionError
from app.utils.uploads import UploadTooLargeError, read_upload_limited

router = APIRouter(prefix="/alibaba1688-imports", tags=["1688订单导入"])


@router.post("/upload")
async def upload_1688_order_file(
    request: Request,
    file: UploadFile = File(...),
    auto_confirm: bool = Query(True, description="上传后立即确认并执行采购自动化"),
    db: Session = Depends(get_db),
) -> dict:
    """上传 1688 官方导出的 XLSX 文件，默认进入 draft 暂存待确认。"""
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="只支持 1688 官方导出的 .xlsx 文件")
    try:
        content = await read_upload_limited(file, max_bytes=service.settings.MAX_UPLOAD_BYTES)
        row, duplicate = service.import_export(
            db,
            content=content,
            original_name=file.filename,
            actor=current_actor(request),
            auto_confirm=auto_confirm,
        )
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    lifecycle = row.lifecycle
    automation = None
    if auto_confirm:
        from app.services.procurement_chain_service import run_full_procurement_automation
        automation = run_full_procurement_automation(db, actor=current_actor(request))
    return {
        "duplicate": duplicate,
        "lifecycle": lifecycle,
        "message": (
            "该文件已导入过"
            if duplicate
            else (
                f"已上传并确认为生效状态，共 {row.imported_order_count} 条订单"
                if lifecycle == "active"
                else f"已上传暂存，共 {row.imported_order_count} 条订单，请在「待确认」中查看并确认导入"
            )
        ),
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


@router.get("/imports/{import_id}")
def get_import_detail(import_id: int, db: Session = Depends(get_db)) -> dict:
    result = service.get_import_detail(db, import_id)
    if result is None:
        raise HTTPException(status_code=404, detail="导入记录不存在")
    return result


@router.get("/imports/{import_id}/orders")
def get_import_orders(import_id: int, db: Session = Depends(get_db)) -> list[dict]:
    result = service.get_import_detail(db, import_id)
    if result is None:
        raise HTTPException(status_code=404, detail="导入记录不存在")
    return result["orders"]


@router.delete("/imports/{import_id}/orders/{order_id}", status_code=204)
def delete_import_order_row(
    request: Request,
    import_id: int,
    order_id: int,
    db: Session = Depends(get_db),
) -> None:
    """明细核对：删除单个订单行（可恢复），业务查询同步排除。"""
    try:
        service.delete_order_row(db, import_id, order_id, actor=current_actor(request))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/imports/{import_id}/orders/{order_id}/restore")
def restore_import_order_row(
    request: Request,
    import_id: int,
    order_id: int,
    db: Session = Depends(get_db),
) -> dict:
    try:
        order = service.restore_order_row(db, import_id, order_id, actor=current_actor(request))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return service.serialize_order(order)


@router.post("/imports/{import_id}/confirm")
def confirm_import(
    request: Request,
    import_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """把 draft 状态的导入确认为 active（出现在采购链路/工作台）。"""
    try:
        row = service.confirm_import(db, import_id, actor=current_actor(request))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except LifecycleTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    result = service.serialize_import(row)
    from app.services.procurement_chain_service import run_full_procurement_automation
    result["automation"] = run_full_procurement_automation(db, actor=current_actor(request))
    return result


@router.delete("/imports/{import_id}", status_code=204)
def soft_delete_import(
    request: Request,
    import_id: int,
    db: Session = Depends(get_db),
) -> None:
    """软删除导入（隐藏到回收站，30 天内可恢复）。"""
    try:
        service.soft_delete_import(db, import_id, actor=current_actor(request))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except LifecycleTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/imports/{import_id}/restore")
def restore_import(
    request: Request,
    import_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """把回收站里的导入恢复到 draft 状态，强制重新确认后再生效。"""
    try:
        row = service.restore_import(db, import_id, actor=current_actor(request))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except LifecycleTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return service.serialize_import(row)
