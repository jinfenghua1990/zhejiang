"""快递物流（成本管理）服务：预估 → 账单 → 核销。

财务铁律：未出账用【预估运费】，已出账用【实际账单金额】，严禁叠加。
发货单量取吉客云销售出库单(outbound)，按 document_at 归月。
预估单价默认 5 元/单，可改（logistics_settings.default_unit_price）。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
import statistics
from zoneinfo import ZoneInfo

from sqlalchemy import func, extract
from sqlalchemy.orm import Session

from app.config import settings as config_settings
from app.models.jackyun import JackyunGoodsDocument
from app.models.logistics import LogisticsBill, LogisticsSetting

DOC_TYPE = "outbound"
DEFAULT_UNIT_PRICE = Decimal("5.00")
_K = "default_unit_price"

STATUS_LABEL = {
    "pending": "待出账",
    "partial": "部分出账",
    "settled": "已核销",
    "abnormal": "异常",
}
INVOICE_LABEL = {"none": "无", "uninvoiced": "未开票", "invoiced": "已开票"}


def _tz() -> ZoneInfo:
    return ZoneInfo(config_settings.TZ)


def _now() -> datetime:
    return datetime.now(_tz())


def default_unit_price(db: Session) -> Decimal:
    row = db.query(LogisticsSetting).filter_by(key=_K).first()
    if row is None:
        return DEFAULT_UNIT_PRICE
    try:
        return Decimal(row.value)
    except Exception:
        return DEFAULT_UNIT_PRICE


def set_default_unit_price(db: Session, value: Decimal) -> Decimal:
    if value < 0:
        raise ValueError("预估单价不能为负数")
    row = db.query(LogisticsSetting).filter_by(key=_K).first()
    if row is None:
        row = LogisticsSetting(key=_K, value=str(value))
        db.add(row)
    else:
        row.value = str(value)
    db.commit()
    return value


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=_tz())
    if month == 12:
        nxt = datetime(year + 1, 1, 1, tzinfo=_tz())
    else:
        nxt = datetime(year, month + 1, 1, tzinfo=_tz())
    return start, nxt


def _shipped_count_range(db: Session, start: datetime, end: datetime) -> int:
    return int(
        db.query(func.count(JackyunGoodsDocument.id))
        .filter(
            JackyunGoodsDocument.document_type == DOC_TYPE,
            JackyunGoodsDocument.document_at.isnot(None),
            JackyunGoodsDocument.document_at >= start,
            JackyunGoodsDocument.document_at < end,
        )
        .scalar()
        or 0
    )


def _monthly_shipped(db: Session, year: int) -> dict[int, int]:
    """当年每月 outbound 出库单数，key=month(1..12)。"""
    rows = (
        db.query(
            extract("month", JackyunGoodsDocument.document_at),
            func.count(JackyunGoodsDocument.id),
        )
        .filter(
            JackyunGoodsDocument.document_type == DOC_TYPE,
            JackyunGoodsDocument.document_at.isnot(None),
            JackyunGoodsDocument.document_at >= datetime(year, 1, 1, tzinfo=_tz()),
            JackyunGoodsDocument.document_at < datetime(year + 1, 1, 1, tzinfo=_tz()),
        )
        .group_by(extract("month", JackyunGoodsDocument.document_at))
        .all()
    )
    return {int(m): int(c) for m, c in rows}


def _cover_month(bill: LogisticsBill, year: int, month: int) -> bool:
    if bill.period_start is None or bill.period_end is None:
        return False
    s, e = _month_bounds(year, month)
    return bill.period_start < e and bill.period_end >= s


def _actual_amount_for_year(db: Session, bill: LogisticsBill, year: int) -> Decimal:
    """跨年度账单只按本年度覆盖部分计入年度物流成本，避免整张账单跨年重复计两次。"""
    if bill.actual_amount is None or bill.period_start is None or bill.period_end is None:
        return Decimal("0")
    tz = bill.period_start.tzinfo or _tz()
    bill_start = datetime.combine(bill.period_start.date(), time.min, tzinfo=tz)
    bill_end = _exclusive_period_end(bill.period_end)
    year_start = datetime(year, 1, 1, tzinfo=_tz())
    year_end = datetime(year + 1, 1, 1, tzinfo=_tz())
    overlap_start = max(bill_start, year_start)
    overlap_end = min(bill_end, year_end)
    if overlap_start >= overlap_end:
        return Decimal("0")

    total_count = _shipped_count_range(db, bill_start, bill_end)
    year_count = _shipped_count_range(db, overlap_start, overlap_end)
    if total_count > 0:
        return bill.actual_amount * Decimal(year_count) / Decimal(total_count)

    total_seconds = Decimal(str((bill_end - bill_start).total_seconds()))
    overlap_seconds = Decimal(str((overlap_end - overlap_start).total_seconds()))
    if total_seconds <= 0:
        return Decimal("0")
    return bill.actual_amount * overlap_seconds / total_seconds


def _settled_bills(db: Session) -> list[LogisticsBill]:
    return (
        db.query(LogisticsBill)
        .filter(LogisticsBill.status.in_(["settled", "partial", "abnormal"]))
        .all()
    )


def settings(db: Session) -> dict:
    return {"defaultUnitPrice": str(default_unit_price(db))}


def historical_models(db: Session) -> dict:
    """从已核销 Excel 账单学习月度/地区实际单均费用。"""
    grouped: dict[tuple[str, str, str, str], list[Decimal]] = defaultdict(list)
    all_fees: list[Decimal] = []

    for bill in _settled_bills(db):
        raw = bill.raw if isinstance(bill.raw, dict) else {}
        if raw.get("format") != "warehouse_logistics_bill_v1":
            continue
        summary = raw.get("summary") if isinstance(raw.get("summary"), dict) else {}
        try:
            direct = Decimal(str(summary.get("directChargeAmount") or "0"))
        except Exception:
            direct = Decimal("0")
        actual = bill.actual_amount or Decimal("0")
        factor = (actual / direct) if direct > 0 else Decimal("1")
        rows = list(raw.get("shipments") or []) + list(raw.get("pickup") or [])
        for row in rows:
            if not isinstance(row, dict):
                continue
            province = str(row.get("province") or "").strip()
            completed = str(row.get("completedAt") or "")
            carrier = str(row.get("carrier") or "").strip()
            weight_band = str(row.get("weightBand") or "").strip() or "未知"
            if not province or len(completed) < 7:
                continue
            try:
                fee = Decimal(str(row.get("fee") or "0")) * factor
            except Exception:
                continue
            if fee <= 0:
                continue
            fee = fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            grouped[(completed[:7], province, carrier, weight_band)].append(fee)
            all_fees.append(fee)

    models: list[dict] = []
    for (month, province, carrier, weight_band), fees in grouped.items():
        avg = sum(fees, Decimal("0")) / Decimal(len(fees))
        med = Decimal(str(statistics.median([float(x) for x in fees])))
        count = len(fees)
        confidence = "high" if count >= 30 else "medium" if count >= 10 else "low"
        models.append({
            "month": month,
            "province": province,
            "carrier": carrier,
            "weightBand": weight_band,
            "sampleCount": count,
            "avgFee": str(avg.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "medianFee": str(med.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "confidence": confidence,
        })
    models.sort(key=lambda row: (row["month"], row["sampleCount"]), reverse=True)

    if all_fees:
        average = sum(all_fees, Decimal("0")) / Decimal(len(all_fees))
        suggested = (average * Decimal("1.05") * Decimal("10")).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        ) / Decimal("10")
        confidence = "high" if len(all_fees) >= 500 else "medium" if len(all_fees) >= 100 else "low"
        smart = {
            "available": True,
            "sampleCount": len(all_fees),
            "averageFee": str(average.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "suggestedUnitPrice": str(suggested.quantize(Decimal("0.01"))),
            "confidence": confidence,
            "method": "历史实际账单（地区/月份/物流公司/重量段）+ 5% 波动余量",
        }
    else:
        smart = {
            "available": False,
            "sampleCount": 0,
            "averageFee": None,
            "suggestedUnitPrice": None,
            "confidence": "none",
            "method": "暂无已核销运单级账单，使用默认预估单价",
        }

    return {"summary": smart, "models": models[:120]}


def workbench(db: Session) -> dict:
    now = _now()
    year, month = now.year, now.month
    unit_price = default_unit_price(db)
    learned = historical_models(db)
    smart_summary = learned["summary"]
    smart_price = Decimal(str(smart_summary["suggestedUnitPrice"])) if smart_summary.get("suggestedUnitPrice") else None
    estimate_unit_price = smart_price if smart_price is not None and smart_price > 0 else unit_price
    estimate_source = "smart" if smart_price is not None and smart_price > 0 else "default"
    shipped = _monthly_shipped(db, year)
    bills = db.query(LogisticsBill).order_by(LogisticsBill.period_start.desc()).all()
    settled = [b for b in bills if b.status in ("settled", "partial", "abnormal")]

    def _est(count: int) -> Decimal:
        return Decimal(count) * estimate_unit_price

    # 1) 当年未出账（未被已核销账单覆盖）的月份 → 独立行
    rows: list[dict] = []
    for m in range(1, month + 1):
        if any(_cover_month(b, year, m) for b in settled):
            continue
        count = shipped.get(m, 0)
        if count <= 0 and m < month:
            continue  # 没有发货数据的过往月份不展示，避免空行
        est = _est(count)
        rows.append(
            {
                "type": "month",
                "period": f"{year}-{m:02d}",
                "periodLabel": f"{year}-{m:02d}",
                "carrier": "",
                "billId": None,
                "shippedCount": count,
                "unitPrice": str(estimate_unit_price),
                "unitPriceSource": estimate_source,
                "estimatedAmount": str(est),
                "actualAmount": None,
                "actualUnitPrice": None,
                "difference": None,
                "status": "pending",
                "statusLabel": "待出账",
                "invoiceStatus": "none",
            }
        )

    # 2) 已核销账单 → 按账期聚合行
    for b in bills:
        if b.status not in ("settled", "partial", "abnormal"):
            continue
        rows.append(
            {
                "type": "bill",
                "period": b.period_label or _range_label(b),
                "periodLabel": b.period_label or _range_label(b),
                "carrier": b.carrier,
                "billId": b.id,
                "shippedCount": b.waybill_count,
                "unitPrice": None,
                "estimatedAmount": str(b.estimated_amount or Decimal("0")),
                "actualAmount": str(b.actual_amount or Decimal("0")),
                "actualUnitPrice": str(b.actual_unit_price) if b.actual_unit_price is not None else None,
                "difference": str(b.difference) if b.difference is not None else None,
                "status": b.status,
                "statusLabel": STATUS_LABEL.get(b.status, b.status),
                "invoiceStatus": b.invoice_status,
            }
        )

    # 排序：数字账期优先（year-month），账单用 period_end 排序 → 统一降序
    rows.sort(key=_row_sort_key, reverse=True)

    cur_month_count = shipped.get(month, 0)
    open_est = sum(
        (Decimal(r["shippedCount"]) * estimate_unit_price) if r["type"] == "month" else Decimal("0")
        for r in rows
    )
    # 本年度物流成本 = 已核销账单在本年度覆盖部分的实际金额 + 未出账月份预估。
    # 跨年度半年账单按当年真实出库单占比分摊，避免同一张账单在两个年度各计全额。
    annual = sum((_actual_amount_for_year(db, b, year) for b in settled), Decimal("0"))
    annual += open_est

    latest_actual = None
    for b in sorted(settled, key=lambda x: (x.period_end or _now()), reverse=True):
        if b.actual_unit_price is not None:
            latest_actual = str(b.actual_unit_price)
            break

    cards = {
        "monthShippedCount": cur_month_count,
        "monthEstimatedAmount": str(_est(cur_month_count)),
        "pendingEstimatedAmount": str(open_est),
        "latestActualUnitPrice": latest_actual,
        "annualLogisticsCost": str(annual),
        "estimateUnitPrice": str(estimate_unit_price),
        "estimateUnitPriceSource": estimate_source,
    }

    return {
        "cards": cards,
        "months": rows,
        "settings": settings(db),
        "smartEstimate": smart_summary,
        "regionalModels": learned["models"],
    }


def _range_label(b: LogisticsBill) -> str:
    def fmt(d: datetime | None) -> str:
        return d.strftime("%Y-%m") if d else ""
    return f"{fmt(b.period_start)} ~ {fmt(b.period_end)}"


def _row_sort_key(row: dict):
    period = row.get("period") or ""
    # 数值化 year，用于排序；账单行用 period_label（如 2026 H1）转换失败给 0
    try:
        return (int(period[:4]), row.get("periodLabel", ""))
    except Exception:
        return (0, period)


def _exclusive_period_end(end: datetime) -> datetime:
    """把账单结束日期统一转换为下一天 00:00 的排他边界。"""
    tz = end.tzinfo or _tz()
    next_day = end.date() + timedelta(days=1)
    return datetime.combine(next_day, time.min, tzinfo=tz)


def _estimate_for_period(db: Session, start: datetime | None, end: datetime | None, unit_price: Decimal) -> Decimal:
    if start is None or end is None or start.date() > end.date():
        return Decimal("0")
    start_tz = start.tzinfo or _tz()
    inclusive_start = datetime.combine(start.date(), time.min, tzinfo=start_tz)
    cnt = _shipped_count_range(db, inclusive_start, _exclusive_period_end(end))
    return Decimal(cnt) * unit_price


def _actual_amount_for_month(
    db: Session, bill: LogisticsBill, year: int, month: int
) -> Decimal:
    """把跨月/半年实际账单按真实发货单量分摊到单个月份。"""
    if bill.actual_amount is None or bill.period_start is None or bill.period_end is None:
        return Decimal("0")
    bill_tz = bill.period_start.tzinfo or _tz()
    bill_start = datetime.combine(bill.period_start.date(), time.min, tzinfo=bill_tz)
    bill_end = _exclusive_period_end(bill.period_end)
    month_start, month_end = _month_bounds(year, month)
    overlap_start = max(bill_start, month_start)
    overlap_end = min(bill_end, month_end)
    if overlap_start >= overlap_end:
        return Decimal("0")

    total_count = _shipped_count_range(db, bill_start, bill_end)
    month_count = _shipped_count_range(db, overlap_start, overlap_end)
    if total_count > 0:
        return bill.actual_amount * Decimal(month_count) / Decimal(total_count)

    total_seconds = Decimal(str((bill_end - bill_start).total_seconds()))
    overlap_seconds = Decimal(str((overlap_end - overlap_start).total_seconds()))
    if total_seconds <= 0:
        return Decimal("0")
    return bill.actual_amount * overlap_seconds / total_seconds


def monthly_finance_cost(db: Session, year: int, month: int) -> dict:
    """财务统一口径：有已核销实际账单则用实际，否则用月度预估。"""
    start, end = _month_bounds(year, month)
    shipped_count = _shipped_count_range(db, start, end)
    covering = [bill for bill in _settled_bills(db) if _cover_month(bill, year, month)]
    if covering:
        amount = sum(
            (_actual_amount_for_month(db, bill, year, month) for bill in covering),
            Decimal("0"),
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return {
            "year": year,
            "month": month,
            "amount": str(amount),
            "valueType": "actual",
            "shippedCount": shipped_count,
            "unitPrice": str((amount / Decimal(shipped_count)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            if shipped_count > 0 else None,
            "source": "settled_bill",
            "billIds": [bill.id for bill in covering],
        }

    learned = historical_models(db)["summary"]
    smart_price = (
        Decimal(str(learned["suggestedUnitPrice"]))
        if learned.get("suggestedUnitPrice") else None
    )
    unit_price = smart_price if smart_price is not None and smart_price > 0 else default_unit_price(db)
    amount = (Decimal(shipped_count) * unit_price).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return {
        "year": year,
        "month": month,
        "amount": str(amount),
        "valueType": "estimated",
        "shippedCount": shipped_count,
        "unitPrice": str(unit_price),
        "source": "smart" if smart_price is not None and smart_price > 0 else "default",
        "billIds": [],
    }


def list_bills(db: Session) -> list[dict]:
    rows = db.query(LogisticsBill).order_by(LogisticsBill.period_start.desc(), LogisticsBill.id.desc()).all()
    return [_bill_dict(b) for b in rows]


def get_bill(db: Session, bill_id: int) -> LogisticsBill | None:
    return db.get(LogisticsBill, bill_id)


def _bill_dict(b: LogisticsBill) -> dict:
    return {
        "id": b.id,
        "periodLabel": b.period_label or _range_label(b),
        "periodStart": b.period_start.isoformat() if b.period_start else None,
        "periodEnd": b.period_end.isoformat() if b.period_end else None,
        "carrier": b.carrier,
        "waybillCount": b.waybill_count,
        "estimatedAmount": str(b.estimated_amount) if b.estimated_amount is not None else None,
        "actualAmount": str(b.actual_amount) if b.actual_amount is not None else None,
        "actualUnitPrice": str(b.actual_unit_price) if b.actual_unit_price is not None else None,
        "difference": str(b.difference) if b.difference is not None else None,
        "status": b.status,
        "statusLabel": STATUS_LABEL.get(b.status, b.status),
        "invoiceStatus": b.invoice_status,
        "invoiceStatusLabel": INVOICE_LABEL.get(b.invoice_status, b.invoice_status),
        "note": b.note,
        "attachmentName": b.attachment_name,
        "importSource": (b.raw or {}).get("source") if isinstance(b.raw, dict) else None,
        "importSummary": (b.raw or {}).get("summary") if isinstance(b.raw, dict) else None,
        "matchedCount": b.matched_count,
        "unmatchedCount": b.unmatched_count,
        "duplicateCount": b.duplicate_count,
        "abnormalCount": b.abnormal_count,
        "createdAt": b.created_at.isoformat() if b.created_at else None,
    }


def create_bill(
    db: Session,
    *,
    period_label: str = "",
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    carrier: str = "",
    waybill_count: int | None = None,
    actual_amount: Decimal,
    invoice_status: str = "none",
    note: str = "",
) -> LogisticsBill:
    unit_price = default_unit_price(db)
    est = _estimate_for_period(db, period_start, period_end, unit_price)
    bill = LogisticsBill(
        period_label=period_label,
        period_start=period_start,
        period_end=period_end,
        carrier=carrier,
        waybill_count=waybill_count,
        estimated_amount=est,
        actual_amount=actual_amount,
        status="pending",
        invoice_status=invoice_status,
        note=note,
    )
    db.add(bill)
    db.commit()
    return bill


def settle_bill(db: Session, bill_id: int) -> LogisticsBill:
    bill = db.get(LogisticsBill, bill_id)
    if bill is None:
        raise KeyError(bill_id)
    if bill.actual_amount is None:
        raise ValueError("账单金额为空，无法核销")
    unit_price = default_unit_price(db)
    est = _estimate_for_period(db, bill.period_start, bill.period_end, unit_price)
    start = bill.period_start
    end = bill.period_end or bill.period_start
    start_tz = start.tzinfo or _tz()
    inclusive_start = datetime.combine(start.date(), time.min, tzinfo=start_tz)
    system_count = _shipped_count_range(db, inclusive_start, _exclusive_period_end(end))
    # Excel 账单有真实包裹/收费记录数时优先使用，避免“一张发货单拆多包裹”把实际单均算高。
    count = int(bill.waybill_count or 0) or system_count
    unit = None
    if count > 0:
        unit = bill.actual_amount / Decimal(count)
    bill.estimated_amount = est
    bill.actual_unit_price = unit
    bill.difference = bill.actual_amount - est
    bill.status = "settled"
    db.commit()
    return bill


def delete_bill(db: Session, bill_id: int) -> None:
    bill = db.get(LogisticsBill, bill_id)
    if bill is None:
        raise KeyError(bill_id)
    db.delete(bill)
    db.commit()
