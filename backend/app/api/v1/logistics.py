"""快递物流（成本管理）API：工作台、账单导入/核销、默认预估单价设置。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import logistics_service as service
from app.services import logistics_import_service as import_service

router = APIRouter(prefix="/logistics", tags=["快递物流"])


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)) -> dict:
    return service.settings(db)


class UnitPriceBody(BaseModel):
    default_unit_price: str


@router.put("/settings")
def put_settings(body: UnitPriceBody, db: Session = Depends(get_db)) -> dict:
    try:
        value = Decimal(body.default_unit_price)
    except Exception:
        raise HTTPException(status_code=400, detail="预估单价格式不正确")
    try:
        service.set_default_unit_price(db, value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return service.settings(db)


@router.get("/workbench")
def workbench(db: Session = Depends(get_db)) -> dict:
    return service.workbench(db)


@router.get("/bills")
def list_bills(db: Session = Depends(get_db)) -> dict:
    return {"items": service.list_bills(db)}


@router.post("/bills/import-xlsx")
async def import_bill_xlsx(
    file: UploadFile = File(...),
    confirm: bool = Query(False),
    db: Session = Depends(get_db),
) -> dict:
    if not file.filename:
        raise HTTPException(status_code=400, detail="请选择物流账单文件")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="物流账单文件为空")
    try:
        return import_service.parse_bill_xlsx(db, content, file.filename, persist=confirm)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/bills/{bill_id}")
def bill_detail(bill_id: int, db: Session = Depends(get_db)) -> dict:
    bill = service.get_bill(db, bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="物流账单不存在")
    return service._bill_dict(bill)


class CreateBillBody(BaseModel):
    period_label: str = ""
    period_start: date | None = None
    period_end: date | None = None
    carrier: str = ""
    waybill_count: int | None = None
    actual_amount: str
    invoice_status: str = "none"
    note: str = ""


@router.post("/bills", status_code=201)
def create_bill(body: CreateBillBody, db: Session = Depends(get_db)) -> dict:
    try:
        amount = Decimal(body.actual_amount)
    except Exception:
        raise HTTPException(status_code=400, detail="账单金额格式不正确")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="账单金额必须大于 0")
    start = datetime.combine(body.period_start, datetime.min.time()) if body.period_start else None
    end = datetime.combine(body.period_end, datetime.max.time()) if body.period_end else None
    bill = service.create_bill(
        db,
        period_label=body.period_label,
        period_start=start,
        period_end=end,
        carrier=body.carrier,
        waybill_count=body.waybill_count,
        actual_amount=amount,
        invoice_status=body.invoice_status,
        note=body.note,
    )
    return service._bill_dict(bill)


@router.post("/bills/{bill_id}/settle")
def settle_bill(bill_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        bill = service.settle_bill(db, bill_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="物流账单不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return service._bill_dict(bill)


@router.delete("/bills/{bill_id}")
def delete_bill(bill_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        service.delete_bill(db, bill_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="物流账单不存在")
    return {"ok": True}
