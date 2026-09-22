from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.db import get_db
from app.models.bank import BankAccount, BankImportBatch, BankTransaction
from app.models.finance import ArchiveFile
from app.models.payment import SettlementRecord
from app.services import finance_service, payment_invoice_match_service
from app.services import reconciliation as rc
from app.utils.uploads import UploadTooLargeError, read_upload_limited

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


# ---------- 映射规则 ----------

@router.get("/rules")
def list_rules(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [
        {"id": r.id, "matchPattern": r.match_pattern, "matchType": r.match_type,
         "platform": r.platform, "enabled": r.enabled, "note": r.note}
        for r in rc.list_rules(db)
    ]


class RuleBody(BaseModel):
    match_pattern: str = Field(min_length=1, max_length=256)
    match_type: Literal["contains", "equals"] = "contains"
    platform: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=2000)


@router.post("/rules")
def create_rule(body: RuleBody, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        row = rc.add_rule(db, match_pattern=body.match_pattern, match_type=body.match_type,
                          platform=body.platform, note=body.note,
                          actor=current_actor(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": row.id}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    rc.delete_rule(db, rule_id, actor=current_actor(request))
    return {"ok": True}


# ---------- 银行流水 ----------

class TxnBody(BaseModel):
    account_no: str = Field(min_length=1, max_length=64)
    txn_date: date
    direction: Literal["in", "out"] = "in"
    amount: str = Field(min_length=1, max_length=64)
    counterparty_name: str = Field(default="", max_length=256)
    counterparty_account: str = Field(default="", max_length=128)
    summary: str = Field(default="", max_length=2000)
    voucher_no: str = Field(default="", max_length=128)


@router.get("/transactions")
def list_transactions(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    direction: Literal["all", "in", "out"] = Query("all"),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    q: str = Query("", max_length=200),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    query = db.query(BankTransaction)
    if direction != "all":
        query = query.filter(BankTransaction.direction == direction)
    if start_date:
        query = query.filter(BankTransaction.txn_date >= start_date)
    if end_date:
        query = query.filter(BankTransaction.txn_date <= end_date)
    needle = q.strip()
    if needle:
        like = f"%{needle}%"
        query = query.filter(
            BankTransaction.counterparty_name.ilike(like)
            | BankTransaction.summary.ilike(like)
            | BankTransaction.serial_no.ilike(like)
            | BankTransaction.voucher_no.ilike(like)
        )
    rows = (
        query.order_by(BankTransaction.txn_date.desc(), BankTransaction.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    settlement_matched_ids = rc.confirmed_settlement_txn_ids(db, [r.id for r in rows])
    row_ids = [r.id for r in rows]
    invoice_statuses = payment_invoice_match_service.txn_reconciliation_statuses(db, row_ids)
    account_ids = {r.account_id for r in rows if r.account_id is not None}
    accounts = {
        account.id: account
        for account in db.query(BankAccount).filter(BankAccount.id.in_(account_ids)).all()
    } if account_ids else {}
    return [
        {
            "id": r.id, "txnDate": r.txn_date.isoformat(), "direction": r.direction,
            "amount": str(r.amount), "counterpartyName": r.counterparty_name,
            "summary": r.summary, "serialNo": r.serial_no, "voucherNo": r.voucher_no,
            "transactionTime": r.transaction_time.isoformat() if r.transaction_time else None,
            "sourceRowNumber": r.source_row_number, "importBatchId": r.import_batch_id,
            "rawAvailable": bool(r.raw),
            "accountSource": (r.raw or {}).get("accountSource", ""),
            # 兼容字段 matched 按流水方向映射到对应域；新代码仍应使用显式域字段。
            "matched": (
                r.id in settlement_matched_ids
                if r.direction == "in"
                else invoice_statuses.get(r.id, {}).get("status") == "matched"
                if r.direction == "out"
                else False
            ),
            "settlementMatched": r.direction == "in" and r.id in settlement_matched_ids,
            "settlementMatchStatus": (
                "matched"
                if r.direction == "in" and r.id in settlement_matched_ids
                else "unmatched"
                if r.direction == "in"
                else "not_applicable"
            ),
            "invoiceMatched": (
                r.direction == "out"
                and invoice_statuses.get(r.id, {}).get("status") == "matched"
            ),
            "invoiceMatchStatus": (
                invoice_statuses.get(r.id, {}).get("status", "unmatched")
                if r.direction == "out"
                else "not_applicable"
            ),
            "invoicePaymentMatched": (
                r.direction == "out"
                and invoice_statuses.get(r.id, {}).get("status") == "matched"
            ),
            "invoicePaymentMatchStatus": (
                invoice_statuses.get(r.id, {}).get("status", "unmatched")
                if r.direction == "out"
                else "not_applicable"
            ),
            "invoiceMatchedAmount": (
                invoice_statuses.get(r.id, {}).get("allocatedAmount", "0.00")
                if r.direction == "out"
                else "0.00"
            ),
            "invoiceRemainingAmount": (
                invoice_statuses.get(r.id, {}).get("remainingAmount", str(r.amount))
                if r.direction == "out"
                else "0.00"
            ),
            "invoicePaymentMatchedAmount": (
                invoice_statuses.get(r.id, {}).get("allocatedAmount", "0.00")
                if r.direction == "out"
                else "0.00"
            ),
            "invoicePaymentRemainingAmount": (
                invoice_statuses.get(r.id, {}).get("remainingAmount", str(r.amount))
                if r.direction == "out"
                else "0.00"
            ),
            "matchStatus": (
                "matched"
                if r.direction == "in" and r.id in settlement_matched_ids
                else "unmatched"
                if r.direction == "in"
                else invoice_statuses.get(r.id, {}).get("status", "unmatched")
                if r.direction == "out"
                else "not_applicable"
            ),
            "settlementMatchedAt": (
                r.matched_at.isoformat()
                if r.direction == "in"
                and r.id in settlement_matched_ids
                and r.matched_at is not None
                else None
            ),
            "invoicePaymentMatchedAt": (
                invoice_statuses.get(r.id, {}).get("matchedAt")
                if r.direction == "out"
                else None
            ),
            # 兼容时间按流水方向映射到对应核对域。
            "matchedAt": (
                r.matched_at.isoformat()
                if r.direction == "in"
                and r.id in settlement_matched_ids
                and r.matched_at is not None
                else invoice_statuses.get(r.id, {}).get("matchedAt")
                if r.direction == "out"
                else None
            ),
            "accountNo": accounts[r.account_id].account_no if r.account_id in accounts else "",
            "accountCode": accounts[r.account_id].internal_code if r.account_id in accounts else "",
            "accountName": accounts[r.account_id].account_name if r.account_id in accounts else "",
            "bankName": accounts[r.account_id].bank_name if r.account_id in accounts else "",
        }
        for r in rows
    ]


@router.get("/transactions/{txn_id}/raw")
def get_transaction_raw(txn_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """读取单笔流水的原始整行和不可覆盖归档文件元数据。"""
    row = db.get(BankTransaction, txn_id)
    if row is None:
        raise HTTPException(404, "银行流水不存在")
    batch = db.get(BankImportBatch, row.import_batch_id) if row.import_batch_id else None
    archive = db.get(ArchiveFile, batch.archive_file_id) if batch and batch.archive_file_id else None
    account = db.get(BankAccount, row.account_id) if row.account_id else None
    return {
        "id": row.id,
        "txnDate": row.txn_date.isoformat(),
        "transactionTime": row.transaction_time.isoformat() if row.transaction_time else None,
        "accountNo": account.account_no if account else "",
        "accountCode": account.internal_code if account else "",
        "serialNo": row.serial_no,
        "voucherNo": row.voucher_no,
        "sourceRowNumber": row.source_row_number,
        "importBatchId": row.import_batch_id,
        "raw": row.raw or {},
        "sourceFile": {
            "id": archive.id,
            "fileName": archive.original_name,
            "sha256": archive.sha256,
            "size": archive.size,
            "version": archive.version,
            "downloadUrl": f"/api/v1/finance/files/{archive.id}/download",
        } if archive else None,
    }


@router.post("/transactions")
def create_transaction(body: TxnBody, request: Request,
                       db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        row, created = rc.add_transaction(
            db, account_no=body.account_no, txn_date=body.txn_date, direction=body.direction,
            amount=body.amount, counterparty_name=body.counterparty_name,
            counterparty_account=body.counterparty_account, summary=body.summary,
            voucher_no=body.voucher_no, actor=current_actor(request),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    from app.services.partner_master_service import rebuild_partner_master
    partner_master = rebuild_partner_master(
        db,
        actor=current_actor(request),
        run_payment_match=True,
    )
    db.commit()
    return {
        "id": row.id,
        "created": created,
        "fingerprint": row.fingerprint[:16],
        "amount": str(row.amount),
        "partnerMaster": partner_master,
    }


@router.post("/import-bank")
async def import_bank_xlsx(
    request: Request,
    file: UploadFile = File(...),
    account_no: str = Form(""),
    period_year: int = Form(...),
    period_month: int = Form(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """上传浙江农信交易明细 XLSX → 解析 → 指纹幂等导入流水。"""
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "流水解析仅支持 XLSX 文件；旧版 XLS 请先另存为 XLSX")
    actor = current_actor(request)
    try:
        content = await read_upload_limited(file, max_bytes=finance_service.settings.MAX_UPLOAD_BYTES)
        if not content:
            raise ValueError("空文件")
        archive = finance_service.store_upload(
            db, company=finance_service.DEFAULT_COMPANY,
            year=period_year, month=period_month, category="bank",
            original_name=file.filename or "bank.xlsx", content=content, actor=actor,
        )
        result = rc.import_bank_xlsx(
            db, account_no=account_no, content=content,
            period_year=period_year, period_month=period_month,
            file_name=file.filename or "bank.xlsx", archive_file_id=archive.id,
            actor=actor,
        )
    except UploadTooLargeError as exc:
        raise HTTPException(413, str(exc))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    from app.services.partner_master_service import rebuild_partner_master
    partner_master = rebuild_partner_master(
        db,
        actor=actor,
        run_payment_match=True,
    )
    db.commit()
    return {"ok": True, **result, "partnerMaster": partner_master}


# ---------- 应收结算 ----------

class SettlementBody(BaseModel):
    platform: str = Field(min_length=1, max_length=64)
    period_year: int
    period_month: int
    expected_amount: str = Field(min_length=1, max_length=64)
    store_name: str = Field(default="", max_length=256)


@router.get("/settlements")
def list_settlements(
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = db.query(SettlementRecord).order_by(
        SettlementRecord.period_year.desc(), SettlementRecord.period_month.desc()
    ).limit(limit).offset(offset).all()
    settled_amounts = rc.settled_amounts_by_settlement(db, [s.id for s in rows])
    out = []
    for s in rows:
        settled = settled_amounts.get(s.id, 0)
        out.append({
            "id": s.id, "platform": s.platform, "storeName": s.store_name,
            "period": f"{s.period_year}-{s.period_month:02d}",
            "expectedAmount": str(s.expected_amount), "settledAmount": str(settled),
            "status": s.status,
        })
    return out


@router.post("/settlements")
def create_settlement(body: SettlementBody, request: Request,
                      db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        row = rc.add_settlement(db, platform=body.platform, period_year=body.period_year,
                                period_month=body.period_month,
                                expected_amount=body.expected_amount, store_name=body.store_name,
                                actor=current_actor(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": row.id}


# ---------- 匹配 ----------

@router.get("/suggestions")
def get_suggestions(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return rc.suggestions(db)


class ConfirmBody(BaseModel):
    txn_id: int = Field(gt=0)
    settlement_id: int = Field(gt=0)


@router.post("/confirm")
def confirm(body: ConfirmBody, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        row = rc.confirm_match(db, txn_id=body.txn_id, settlement_id=body.settlement_id,
                               actor=current_actor(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": row.id, "score": row.score, "confidence": row.confidence}


@router.post("/reject")
def reject(body: ConfirmBody, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    rc.reject_match(db, txn_id=body.txn_id, settlement_id=body.settlement_id,
                    actor=current_actor(request))
    return {"ok": True}


@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> dict[str, Any]:
    return rc.overview(db)
