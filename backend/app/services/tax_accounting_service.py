"""税务做账真值层：已确认官方发票优先，业务系统仅用于差异对账。

核心原则：
1. 金额、税额以 active 的税务官方发票清单为准；
2. 数量、单价仅在官方发票原始行明确提供时作为做账真值；
3. 发票缺少数量/单价时保持缺失，不用吉客云、1688 或手工数据兜底；
4. 吉客云/业务单据只用于对账和差异提示，不能覆盖税务真值。
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.sales import SalesOrder, SalesOrderItem
from app.models.tax import TaxInvoice, TaxInvoiceImport, TaxInvoiceLink
from app.services.monthly_core import month_bounds
from app.services.tax_invoice_service import is_effective_for_accounting
from app.utils.money import to_decimal


LINE_ALIASES: dict[str, tuple[str, ...]] = {
    "goods_name": (
        "货物或应税劳务、服务名称", "货物或应税劳务服务名称", "项目名称", "商品名称",
        "货物名称", "服务名称", "品名", "名称",
    ),
    "specification": ("规格型号", "规格", "型号"),
    "unit": ("单位", "计量单位"),
    "quantity": ("数量", "开票数量", "商品数量"),
    "unit_price_excl_tax": ("单价", "不含税单价", "单价(不含税)", "单价（不含税）"),
    "amount_excl_tax": ("不含税金额", "金额(不含税)", "金额（不含税）", "金额"),
    "tax_rate": ("税率", "征收率"),
    "tax_amount": ("税额", "税金"),
    "tax_code": ("税收分类编码", "商品和服务税收分类编码", "税收编码"),
}


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    return re.sub(r"[\s_\-()（）\[\]【】/\\:：]+", "", text)


def _pick(raw: dict[str, Any], aliases: tuple[str, ...]) -> str:
    normalized = [(_norm(key), str(value or "").strip()) for key, value in (raw or {}).items()]
    alias_keys = [_norm(alias) for alias in aliases]
    for alias in alias_keys:
        for key, value in normalized:
            if key == alias and value:
                return value
    for alias in alias_keys:
        if len(alias) < 3:
            continue
        for key, value in normalized:
            if alias in key and value:
                return value
    return ""


def _decimal(value: object) -> Decimal | None:
    text = str(value or "").strip().replace(",", "").replace("，", "")
    text = text.replace("¥", "").replace("￥", "").replace("元", "").replace(" ", "")
    if not text or text in {"-", "--", "/", "—"}:
        return None
    if text.endswith("%"):
        text = text[:-1]
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        value_dec = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return -value_dec if negative else value_dec


def _money(value: Decimal | None) -> str | None:
    return None if value is None else f"{value.quantize(Decimal('0.01')):f}"


def _qty(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value.normalize():f}"


def _line_truth(invoice: TaxInvoice) -> dict[str, Any]:
    raw = invoice.raw or {}
    quantity = _decimal(_pick(raw, LINE_ALIASES["quantity"]))
    unit_price = _decimal(_pick(raw, LINE_ALIASES["unit_price_excl_tax"]))
    line_amount = _decimal(_pick(raw, LINE_ALIASES["amount_excl_tax"]))
    tax_amount = _decimal(_pick(raw, LINE_ALIASES["tax_amount"]))
    tax_rate_text = _pick(raw, LINE_ALIASES["tax_rate"])
    tax_rate = _decimal(tax_rate_text)

    # 只有官方行同时有数量和金额时才允许反算单价；绝不从业务系统反推税务单价。
    derived_unit_price = False
    if unit_price is None and quantity not in (None, Decimal("0")) and line_amount is not None:
        unit_price = line_amount / quantity
        derived_unit_price = True

    detail_complete = quantity is not None and unit_price is not None
    return {
        "goodsName": _pick(raw, LINE_ALIASES["goods_name"]),
        "specification": _pick(raw, LINE_ALIASES["specification"]),
        "unit": _pick(raw, LINE_ALIASES["unit"]),
        "quantity": _qty(quantity),
        "unitPriceExclTax": _money(unit_price),
        "lineAmountExclTax": _money(line_amount),
        "lineTaxAmount": _money(tax_amount),
        "taxRate": None if tax_rate is None else f"{tax_rate.normalize():f}",
        "taxCode": _pick(raw, LINE_ALIASES["tax_code"]),
        "detailComplete": detail_complete,
        "unitPriceDerivedFromInvoiceLine": derived_unit_price,
    }


def monthly_ledger(db: Session, year: int, month: int) -> dict[str, Any]:
    if year < 2000 or year > 9999 or month < 1 or month > 12:
        raise ValueError("账期不正确")

    start, end = month_bounds(year, month)

    # 只把已确认(active)的官方税务导入作为做账真值来源。
    rows = (
        db.query(TaxInvoice)
        .join(TaxInvoiceImport, TaxInvoiceImport.id == TaxInvoice.source_import_id)
        .filter(
            TaxInvoiceImport.lifecycle == "active",
            TaxInvoice.source_system == "tax_export",
            TaxInvoice.issue_date >= start,
            TaxInvoice.issue_date < end,
        )
        .order_by(TaxInvoice.issue_date, TaxInvoice.id)
        .all()
    )

    invoice_ids = [row.id for row in rows]
    links = (
        db.query(TaxInvoiceLink)
        .filter(TaxInvoiceLink.invoice_id.in_(invoice_ids), TaxInvoiceLink.confirmed.is_(True))
        .all()
        if invoice_ids else []
    )
    links_by_invoice: dict[int, list[TaxInvoiceLink]] = defaultdict(list)
    for link in links:
        links_by_invoice[link.invoice_id].append(link)

    sales_order_ids = {link.target_id for link in links if link.target_type == "sales_order"}
    sales_orders = (
        {row.id: row for row in db.query(SalesOrder).filter(SalesOrder.id.in_(sales_order_ids)).all()}
        if sales_order_ids else {}
    )
    sales_qty = (
        {
            int(order_id): to_decimal(quantity)
            for order_id, quantity in (
                db.query(SalesOrderItem.order_id, func.coalesce(func.sum(SalesOrderItem.quantity), 0))
                .filter(SalesOrderItem.order_id.in_(sales_order_ids))
                .group_by(SalesOrderItem.order_id)
                .all()
            )
        }
        if sales_order_ids else {}
    )

    totals = {
        "output": {"amountExclTax": Decimal("0"), "taxAmount": Decimal("0"), "totalAmount": Decimal("0")},
        "input": {"amountExclTax": Decimal("0"), "taxAmount": Decimal("0"), "totalAmount": Decimal("0")},
    }
    verified_input_tax = Decimal("0")
    items: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    amount_diff_count = 0
    quantity_diff_count = 0

    for invoice in rows:
        direction = invoice.direction if invoice.direction in {"input", "output"} else "unknown"
        is_effective = is_effective_for_accounting(invoice)
        if direction in totals and is_effective:
            totals[direction]["amountExclTax"] += to_decimal(invoice.amount_excl_tax)
            totals[direction]["taxAmount"] += to_decimal(invoice.tax_amount)
            totals[direction]["totalAmount"] += to_decimal(invoice.total_amount)
        if direction == "input" and is_effective and invoice.verified:
            verified_input_tax += to_decimal(invoice.tax_amount)

        line = _line_truth(invoice)
        business_amount: Decimal | None = None
        business_quantity: Decimal | None = None
        business_refs: list[str] = []
        for link in links_by_invoice.get(invoice.id, []):
            if link.target_type != "sales_order":
                business_refs.append(f"{link.target_type}#{link.target_id}")
                continue
            sales = sales_orders.get(link.target_id)
            if sales is not None:
                business_refs.append(sales.order_no)
                candidate = sales.paid_amount if sales.paid_amount is not None else sales.order_amount
                if candidate is not None:
                    business_amount = (
                        to_decimal(candidate)
                        if business_amount is None
                        else business_amount + to_decimal(candidate)
                    )
                business_quantity = (
                    sales_qty.get(sales.id, Decimal("0"))
                    if business_quantity is None
                    else business_quantity + sales_qty.get(sales.id, Decimal("0"))
                )

        invoice_total = to_decimal(invoice.total_amount) if invoice.total_amount is not None else None
        invoice_quantity = _decimal(line["quantity"])
        amount_diff = None if invoice_total is None or business_amount is None else invoice_total - business_amount
        quantity_diff = None if invoice_quantity is None or business_quantity is None else invoice_quantity - business_quantity
        if amount_diff is not None and abs(amount_diff) >= Decimal("0.01"):
            amount_diff_count += 1
        if quantity_diff is not None and abs(quantity_diff) >= Decimal("0.0001"):
            quantity_diff_count += 1

        reasons: list[str] = []
        if direction == "unknown":
            reasons.append("发票方向未确认")
        if invoice.status == "unknown":
            reasons.append("发票状态未确认")
        if invoice.total_amount is None:
            reasons.append("官方发票缺少价税合计")
        if direction == "output" and is_effective and not line["detailComplete"]:
            reasons.append("官方发票清单缺少数量或单价；禁止用吉客云/手工数据替代")
        if amount_diff is not None and abs(amount_diff) >= Decimal("0.01"):
            reasons.append("开票金额与业务系统金额不一致，以开票金额为准，需核对业务数据")
        if quantity_diff is not None and abs(quantity_diff) >= Decimal("0.0001"):
            reasons.append("开票数量与业务系统数量不一致，以开票数量为准，需核对业务数据")
        if reasons:
            blockers.append({"invoiceId": invoice.id, "invoiceNumber": invoice.invoice_number, "reasons": reasons})

        items.append({
            "invoiceId": invoice.id,
            "invoiceNumber": invoice.invoice_number,
            "direction": direction,
            "status": invoice.status,
            "issueDate": invoice.issue_date.isoformat() if invoice.issue_date else None,
            "sellerName": invoice.seller_name,
            "buyerName": invoice.buyer_name,
            "amountExclTax": _money(invoice.amount_excl_tax),
            "taxAmount": _money(invoice.tax_amount),
            "totalAmount": _money(invoice.total_amount),
            "verified": bool(invoice.verified),
            "verifiedMonth": invoice.verified_month or "",
            "invoiceLine": line,
            "businessRefs": business_refs,
            "businessAmount": _money(business_amount),
            "businessQuantity": _qty(business_quantity),
            "amountDifference": _money(amount_diff),
            "quantityDifference": _qty(quantity_diff),
            "accountingSource": "tax_invoice",
            "businessDataRole": "reconciliation_only",
        })

    output_tax = totals["output"]["taxAmount"]
    estimated_vat_balance = output_tax - verified_input_tax
    return {
        "period": f"{year}-{month:02d}",
        "policy": {
            "accountingTruth": "confirmed_official_tax_invoice",
            "amountPriority": "tax_invoice",
            "quantityPriority": "tax_invoice_detail_only",
            "unitPricePriority": "tax_invoice_detail_only",
            "businessDataRole": "reconciliation_only",
            "fallbackToBusinessWhenInvoiceMissing": False,
            "note": "开票数据与吉客云/1688/手工数据冲突时，做账采用开票数据；业务数据保留差异并要求核对。",
        },
        "summary": {
            "invoiceCount": len(rows),
            "outputInvoiceCount": sum(1 for row in rows if row.direction == "output" and is_effective_for_accounting(row)),
            "inputInvoiceCount": sum(1 for row in rows if row.direction == "input" and is_effective_for_accounting(row)),
            "outputAmountExclTax": _money(totals["output"]["amountExclTax"]),
            "outputTaxAmount": _money(output_tax),
            "outputTotalAmount": _money(totals["output"]["totalAmount"]),
            "inputAmountExclTax": _money(totals["input"]["amountExclTax"]),
            "inputTaxAmount": _money(totals["input"]["taxAmount"]),
            "inputTotalAmount": _money(totals["input"]["totalAmount"]),
            "verifiedInputTaxAmount": _money(verified_input_tax),
            "estimatedVatBeforeOtherAdjustments": _money(estimated_vat_balance),
            "amountDifferenceCount": amount_diff_count,
            "quantityDifferenceCount": quantity_diff_count,
            "blockerCount": len(blockers),
            "readyForAccountingDraft": len(blockers) == 0 and len(rows) > 0,
        },
        "blockers": blockers,
        "items": items,
    }
