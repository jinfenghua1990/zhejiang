from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.db import get_db
from app.models.finance_voucher import FinanceVoucher, FinanceVoucherLine
from app.services import finance_voucher_service

router = APIRouter(prefix="/finance/vouchers", tags=["finance-vouchers"])


class GenerateRequest(BaseModel):
    legal_entity_id: int
    year: int = Field(ge=1900, le=2999)
    month: int = Field(ge=1, le=12)


def _serialize_voucher(v: FinanceVoucher, db: Session) -> dict:
    lines = db.scalars(
        select(FinanceVoucherLine).where(FinanceVoucherLine.voucher_id == v.id).order_by(FinanceVoucherLine.seq)
    ).all()
    debit = sum((l.amount for l in lines if l.direction == "debit"), Decimal("0"))
    credit = sum((l.amount for l in lines if l.direction == "credit"), Decimal("0"))
    return {
        "id": v.id,
        "voucherNo": v.voucher_no,
        "year": v.accounting_year,
        "month": v.accounting_month,
        "voucherDate": v.voucher_date.isoformat() if v.voucher_date else None,
        "currency": v.currency,
        "source": v.source,
        "status": v.status,
        "note": v.note,
        "debitTotal": float(debit),
        "creditTotal": float(credit),
        "balanced": abs(debit - credit) <= Decimal("0.01"),
        "lines": [
            {
                "seq": l.seq,
                "accountCode": l.account_code,
                "accountName": l.account_name,
                "direction": l.direction,
                "amount": float(l.amount),
                "taxAmount": float(l.tax_amount),
                "summary": l.summary,
                "entryId": l.entry_id,
            }
            for l in lines
        ],
    }


@router.post("")
def generate(req: GenerateRequest, db: Session = Depends(get_db),
             actor: str = Depends(current_actor)) -> dict:
    try:
        result = finance_voucher_service.generate_vouchers_for_period(
            db, legal_entity_id=req.legal_entity_id, year=req.year, month=req.month, actor=actor
        )
        db.commit()
        return result
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="自动凭证保存失败") from e


@router.get("")
def list_vouchers(legal_entity_id: int, year: int, month: int,
                  db: Session = Depends(get_db)) -> list[dict]:
    vouchers = db.scalars(
        select(FinanceVoucher).where(
            FinanceVoucher.legal_entity_id == legal_entity_id,
            FinanceVoucher.accounting_year == year,
            FinanceVoucher.accounting_month == month,
        ).order_by(FinanceVoucher.voucher_no)
    ).all()
    return [_serialize_voucher(v, db) for v in vouchers]


@router.get("/{voucher_id}")
def get_voucher(voucher_id: int, db: Session = Depends(get_db)) -> dict:
    v = db.get(FinanceVoucher, voucher_id)
    if v is None:
        raise HTTPException(status_code=404, detail="凭证不存在")
    return _serialize_voucher(v, db)


@router.post("/{voucher_id}/post")
def post_voucher(voucher_id: int, db: Session = Depends(get_db),
                 actor: str = Depends(current_actor)) -> dict:
    v = db.get(FinanceVoucher, voucher_id)
    if v is None:
        raise HTTPException(status_code=404, detail="凭证不存在")
    if v.status == "posted":
        raise HTTPException(status_code=400, detail="凭证已过账")
    lines = db.scalars(
        select(FinanceVoucherLine).where(FinanceVoucherLine.voucher_id == v.id)
    ).all()
    debit = sum((l.amount for l in lines if l.direction == "debit"), Decimal("0"))
    credit = sum((l.amount for l in lines if l.direction == "credit"), Decimal("0"))
    if abs(debit - credit) > Decimal("0.01"):
        raise HTTPException(status_code=400, detail="借贷不平衡，禁止过账：差 " + str(debit - credit))
    v.status = "posted"
    db.commit()
    return {"id": v.id, "status": v.status}
