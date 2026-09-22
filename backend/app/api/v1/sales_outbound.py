"""销售出库报表 API：实时查询 + CSV 导出（财务月度自动化第一形态）。"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.db import get_db
from app.services import finance_service, sales_outbound_report_service as svc

router = APIRouter(prefix="/sales-outbound", tags=["sales-outbound"])


@router.get("/months")
def list_months(db: Session = Depends(get_db)) -> list[dict]:
    """有出库单数据的账期列表（年/月/单据数）。"""
    return svc.months_with_data(db)


@router.get("/report")
def report(
    year: int | None = Query(None, description="账期年；省略则取最新有数据的月份"),
    month: int | None = Query(None, description="账期月 1-12"),
    db: Session = Depends(get_db),
) -> dict:
    """单月销售出库聚合：汇总 + 按仓库 + 按货品 + 明细。"""
    if year is None or month is None:
        ms = svc.months_with_data(db)
        if not ms:
            today = date.today()
            return {
                "year": year or today.year,
                "month": month or today.month,
                "empty": True,
                "hasAmount": False,
                "summary": {"docCount": 0, "totalQuantity": "0", "totalAmount": None,
                            "skuCount": 0, "warehouseCount": 0},
                "byWarehouse": [], "bySku": [], "items": [],
            }
        latest = ms[-1]
        year, month = latest["year"], latest["month"]
    return svc.build_report(db, year, month)


@router.get("/report.csv")
def report_csv(
    year: int | None = Query(None),
    month: int | None = Query(None),
    db: Session = Depends(get_db),
) -> Response:
    """导出单月销售出库明细 CSV（utf-8-sig）。"""
    if year is None or month is None:
        ms = svc.months_with_data(db)
        if ms:
            year, month = ms[-1]["year"], ms[-1]["month"]
        else:
            today = date.today()
            year, month = today.year, today.month
    rep = svc.build_report(db, year, month)
    csv_bytes = svc.to_csv(rep)
    headers = {
        "Content-Disposition": f'attachment; filename="sales_outbound_{year}{month:02d}.csv"'
    }
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers=headers,
    )


@router.post("/archive")
def archive_now(
    request: Request,
    year: int = Query(...),
    month: int = Query(...),
    db: Session = Depends(get_db),
) -> dict:
    """按需把指定账期销售出库报表生成 CSV 并归档进财务资料中心（category=jackyun）。

    与每月 2 日定时任务同逻辑；供页面「归档本月」按钮即时调用。
    """
    rep = svc.build_report(db, year, month)
    csv_bytes = svc.to_csv(rep)
    row = finance_service.store_upload(
        db,
        company=finance_service.DEFAULT_COMPANY,
        year=year, month=month,
        category="jackyun",
        original_name=f"销售出库_{year}{month:02d}.csv",
        content=csv_bytes,
        actor=current_actor(request),
    )
    return {
        "ok": True,
        "period": f"{year}-{month:02d}",
        "archiveFileId": row.id,
        "version": row.version,
        "docCount": rep["summary"]["docCount"],
        "hasAmount": rep["hasAmount"],
    }
