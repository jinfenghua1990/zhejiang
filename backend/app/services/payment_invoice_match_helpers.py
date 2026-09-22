"""支付-发票核对领域的基础辅助函数。

从 payment_invoice_match_service 拆出，保持可复用且不依赖主服务的纯函数/
轻查询；主服务通过 from ... import 引用，外部调用方接口不变。
本文件受 test_domain_architecture.py 保护：禁止直写发票事实字段。
"""
from __future__ import annotations

import calendar
import re
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.tax import TaxInvoice, TaxInvoiceLink
from app.services import tax_invoice_service
from app.services.monthly_core import month_bounds
from app.utils.money import quantize, to_decimal

TARGET_TYPE = "bank_transaction"
TOLERANCE = Decimal("0.01")
MONEY_QUANT = Decimal("0.01")
# 匹配窗口：发票日期 ±3 个月内的流水都视为同一笔交易
MATCH_WINDOW_MONTHS = 3


def _normalize_name(name: str | None) -> str:
    """统一名称：去全/半角括号差异、去所有空白，便于匹配。"""
    if not name:
        return ""
    text = str(name).strip()
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"\s+", "", text)
    return text


def _normalize_account(value: str | None) -> str:
    """银行账号只去格式字符，不做模糊匹配。"""
    return re.sub(r"[\s-]+", "", str(value or "").strip()).upper()


def _normalize_tax_no(value: str | None) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).upper()


def _invoice_issue_date(invoice: TaxInvoice) -> date | None:
    value = invoice.issue_date
    if value is None:
        return None
    return value.date() if hasattr(value, "date") else value


def _month_range(year: int, month: int) -> tuple[date, date]:
    if not (1 <= month <= 12):
        raise ValueError("非法账期")
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def _offset_month(year: int, month: int, offset: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + offset
    target_year, zero_based_month = divmod(index, 12)
    return target_year, zero_based_month + 1


def _invoice_candidate_window(year: int, month: int):
    """当前账期银行流水可匹配的发票窗口，统一使用业务时区 [start, end)。"""
    start_year, start_month = _offset_month(year, month, -MATCH_WINDOW_MONTHS)
    end_year, end_month = _offset_month(year, month, MATCH_WINDOW_MONTHS)
    start, _ = month_bounds(start_year, start_month)
    _, end = month_bounds(end_year, end_month)
    return start, end


def _dec(value: Decimal | int | str | None) -> Decimal:
    return quantize(to_decimal(value), MONEY_QUANT)


def _invoice_target_amount(db: Session, invoice: TaxInvoice) -> Decimal:
    """银行付款核对以红冲后的有效蓝字余额为上限，原票金额仍保留在发票台账。"""
    return _dec(tax_invoice_service.effective_invoice_amount_after_red(db, invoice))


def _link_amount(link: TaxInvoiceLink, invoice: TaxInvoice | None) -> Decimal:
    if link.allocated_amount is not None:
        return _dec(link.allocated_amount)
    if invoice is not None and invoice.total_amount is not None:
        return _dec(invoice.total_amount)
    return Decimal("0.0000")


def _active_bank_links(db: Session, *, invoice_id: int | None = None, txn_id: int | None = None,
                       exclude_link_id: int | None = None) -> list[TaxInvoiceLink]:
    q = db.query(TaxInvoiceLink).filter(
        TaxInvoiceLink.target_type == TARGET_TYPE,
        TaxInvoiceLink.match_method != "rejected",
        TaxInvoiceLink.confirmed.is_(True),
    )
    if invoice_id is not None:
        q = q.filter(TaxInvoiceLink.invoice_id == invoice_id)
    if txn_id is not None:
        q = q.filter(TaxInvoiceLink.target_id == txn_id)
    if exclude_link_id is not None:
        q = q.filter(TaxInvoiceLink.id != exclude_link_id)
    return q.all()


def _invoice_bank_allocated(db: Session, invoice_id: int, *, exclude_link_id: int | None = None) -> Decimal:
    links = _active_bank_links(db, invoice_id=invoice_id, exclude_link_id=exclude_link_id)
    invoice = db.get(TaxInvoice, invoice_id)
    return sum(
        (_link_amount(row, invoice) for row in links),
        Decimal("0.0000"),
    )


def _txn_bank_allocated(db: Session, txn_id: int, *, exclude_link_id: int | None = None) -> Decimal:
    links = _active_bank_links(db, txn_id=txn_id, exclude_link_id=exclude_link_id)
    invoice_ids = {row.invoice_id for row in links if row.allocated_amount is None}
    invoice_map = {
        row.id: row
        for row in db.query(TaxInvoice).filter(TaxInvoice.id.in_(invoice_ids)).all()
    } if invoice_ids else {}
    return sum(
        (_link_amount(row, invoice_map.get(row.invoice_id)) for row in links),
        Decimal("0.0000"),
    )


def _invoice_brief(invoice: TaxInvoice, link: TaxInvoiceLink | None = None) -> dict[str, Any]:
    return {
        "linkId": link.id if link else None,
        "invoiceId": invoice.id,
        "invoiceNumber": invoice.invoice_number,
        "sellerName": invoice.seller_name,
        "issueDate": invoice.issue_date.isoformat()[:10] if invoice.issue_date else "",
        "totalAmount": str(_dec(invoice.total_amount)),
        "allocatedAmount": str(_link_amount(link, invoice)) if link else None,
        "legacyAllocatedAmountMissing": bool(link and link.allocated_amount is None),
    }


def _status(remaining: Decimal, amount: Decimal) -> str:
    """按实际已分摊金额判断，不能把“0 元已匹配”因容差误判为 matched。"""
    amount = _dec(amount)
    remaining = _dec(remaining)
    matched_amount = amount - remaining
    if matched_amount <= 0:
        return "unmatched"
    if remaining <= TOLERANCE:
        return "matched"
    return "partial"