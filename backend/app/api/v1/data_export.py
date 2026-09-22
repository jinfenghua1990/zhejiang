from __future__ import annotations

from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import data_export_service as service
from app.services import master_data_import_service as import_service
from app.api.deps import current_actor, require_roles
from app.models.org import User
from app.utils.uploads import UploadTooLargeError, read_upload_limited

router = APIRouter(prefix="/data", tags=["数据导出"])


@router.get("/export-options")
def export_options() -> list[dict[str, str]]:
    """返回网页数据中心可用的导出数据集。"""
    return list(service.DATASET_OPTIONS)


@router.get("/export/{dataset}")
def export_dataset(
    dataset: str,
    status: str = Query(""),
    q: str = Query(""),
    channel: str = Query("all"),
    kind: str = Query("all"),
    warehouse: str = Query(""),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    group: str = Query("all"),
    stage: str = Query(""),
    template: bool = Query(False, description="只导出表头（空模板），供填写后重新导入"),
    db: Session = Depends(get_db),
) -> Response:
    """导出本地业务事实为 XLSX，供人工核验；不会触发同步或写入数据库。"""
    try:
        content, count, label = service.build_export(
            db,
            dataset,
            status=status,
            q=q,
            channel=channel,
            kind=kind,
            warehouse=warehouse,
            start_date=start_date,
            end_date=end_date,
            group=group,
            stage=stage,
            template=template,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    filename = f"{label}-{'模板' if template else '核验'}-{date.today().isoformat()}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "X-Export-Row-Count": str(count),
            "Cache-Control": "no-store",
        },
    )


@router.post("/import/{dataset}")
async def import_master_dataset(
    dataset: str,
    request: Request,
    _editor: User = Depends(require_roles("admin", "operator")),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    """回导基础档案模板：先整表校验，再按档案主键/业务编码增量更新。"""
    try:
        content = await read_upload_limited(file, max_bytes=20 * 1024 * 1024)
        result = import_service.import_dataset(
            db,
            dataset,
            content,
            file.filename or f"{dataset}.xlsx",
            current_actor(request),
        )
    except UploadTooLargeError as exc:
        raise HTTPException(413, str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **result}
