"""统一财务中心：公司主体 + 财务事项池。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import FinanceEntry, FinanceLegalEntity

DEFAULT_ENTITY_CODE = "ZJCB"
DEFAULT_ENTITY_NAME = "浙江柴本网络科技有限公司"

CATEGORY_LABELS = {
    "sales_income": "销售收入",
    "purchase_cost": "采购成本",
    "inventory_purchase": "库存采购 / 应付",
    "sales_cost": "销售成本",
    "shipment_goods_cost": "出运货品成本",
    "cargo_insurance": "货运保险",
    "port_fee": "港杂 / 码头费",
    "platform_fee": "平台费用",
    "domestic_logistics": "国内物流",
    "international_freight": "国际物流",
    "export_fee": "出口费用",
    "export_tax_refund": "出口退税",
    "customs_duty": "进口关税",
    "anti_dumping_duty": "反倾销税",
    "countervailing_duty": "反补贴税",
    "import_vat": "进口 VAT",
    "clearance_fee": "清关费用",
    "last_mile_fee": "海外末端配送",
    "refund": "退款",
    "exchange_gain_loss": "汇兑损益",
    "other": "其他",
}


def _decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def ensure_default_entity(db: Session) -> FinanceLegalEntity:
    """只读路径兜底：返回默认主体；缺失时仅加入当前事务（不提交）。

    持久化由启动 seed（app.seed.ensure_seed）保证，避免 GET 请求在
    读路径上执行 commit 造成副作用。
    """
    row = db.scalar(select(FinanceLegalEntity).where(FinanceLegalEntity.code == DEFAULT_ENTITY_CODE))
    if row is not None:
        return row
    row = FinanceLegalEntity(
        code=DEFAULT_ENTITY_CODE,
        name=DEFAULT_ENTITY_NAME,
        country_code="CN",
        base_currency="CNY",
        status="active",
        is_default=True,
        business_scopes=["domestic", "foreign_trade"],
        note="系统初始化默认主体",
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def entity_dict(row: FinanceLegalEntity) -> dict[str, Any]:
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "countryCode": row.country_code,
        "baseCurrency": row.base_currency,
        "taxId": row.tax_id,
        "status": row.status,
        "isDefault": bool(row.is_default),
        "businessScopes": row.business_scopes or [],
        "note": row.note,
    }


def list_entities(db: Session) -> list[dict[str, Any]]:
    ensure_default_entity(db)
    rows = db.scalars(
        select(FinanceLegalEntity)
        .where(FinanceLegalEntity.status != "archived")
        .order_by(FinanceLegalEntity.is_default.desc(), FinanceLegalEntity.id.asc())
    ).all()
    return [entity_dict(row) for row in rows]


def resolve_entity(db: Session, legal_entity_id: int | None = None) -> FinanceLegalEntity:
    if legal_entity_id is not None:
        row = db.get(FinanceLegalEntity, legal_entity_id)
        if row is None or row.status == "archived":
            raise ValueError("公司主体不存在")
        return row
    default = db.scalar(
        select(FinanceLegalEntity)
        .where(FinanceLegalEntity.is_default.is_(True))
        .where(FinanceLegalEntity.status != "archived")
        .order_by(FinanceLegalEntity.id.asc())
    )
    return default or ensure_default_entity(db)


def entry_dict(row: FinanceEntry, entity: FinanceLegalEntity | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "legalEntityId": row.legal_entity_id,
        "legalEntityName": entity.name if entity else "",
        "businessScope": row.business_scope,
        "sourceType": row.source_type,
        "sourceId": row.source_id,
        "sourceNo": row.source_no,
        "category": row.category,
        "categoryLabel": CATEGORY_LABELS.get(row.category, row.category),
        "direction": row.direction,
        "cashEffect": bool(row.cash_effect),
        "profitEffect": bool(row.profit_effect),
        "currency": row.currency,
        "amount": str(row.amount or 0),
        "taxAmount": str(row.tax_amount or 0),
        "valueType": row.value_type,
        "settlementStatus": row.settlement_status,
        "invoiceStatus": row.invoice_status,
        "accountingYear": row.accounting_year,
        "accountingMonth": row.accounting_month,
        "occurredAt": row.occurred_at.isoformat() if row.occurred_at else None,
        "note": row.note,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_entries(
    db: Session,
    *,
    legal_entity_id: int | None = None,
    business_scope: str = "all",
    year: int | None = None,
    month: int | None = None,
    status: str = "",
    limit: int = 200,
) -> list[dict[str, Any]]:
    entity = resolve_entity(db, legal_entity_id)
    stmt = select(FinanceEntry).where(FinanceEntry.legal_entity_id == entity.id)
    if business_scope in {"domestic", "foreign_trade"}:
        stmt = stmt.where(FinanceEntry.business_scope == business_scope)
    if year is not None:
        stmt = stmt.where(FinanceEntry.accounting_year == year)
    if month is not None:
        stmt = stmt.where(FinanceEntry.accounting_month == month)
    if status:
        stmt = stmt.where(FinanceEntry.settlement_status == status)
    stmt = stmt.order_by(FinanceEntry.id.desc()).limit(limit)
    return [entry_dict(row, entity) for row in db.scalars(stmt).all()]


def center_overview(
    db: Session,
    *,
    legal_entity_id: int | None = None,
    business_scope: str = "all",
    year: int | None = None,
    month: int | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    year = year or now.year
    month = month or now.month
    entity = resolve_entity(db, legal_entity_id)

    stmt = (
        select(FinanceEntry)
        .where(FinanceEntry.legal_entity_id == entity.id)
        .where(FinanceEntry.accounting_year == year)
        .where(FinanceEntry.accounting_month == month)
    )
    if business_scope in {"domestic", "foreign_trade"}:
        stmt = stmt.where(FinanceEntry.business_scope == business_scope)
    rows = db.scalars(stmt.order_by(FinanceEntry.id.desc())).all()

    totals: dict[str, dict[str, Decimal]] = {}
    scopes = {
        "domestic": {"count": 0, "estimated": 0, "pending": 0},
        "foreign_trade": {"count": 0, "estimated": 0, "pending": 0},
    }
    todo = {
        "pendingConfirmation": 0,
        "pendingSettlement": 0,
        "pendingInvoice": 0,
        "estimated": 0,
        "anomalies": 0,
    }

    # 同一来源/类别存在“预计 + 实际”时，经营预测用实际替代预计，避免重复计算。
    actual_keys = {
        (row.source_type, row.source_id, row.category)
        for row in rows
        if row.value_type == "actual"
    }

    for row in rows:
        bucket = totals.setdefault(
            row.currency,
            {
                "actualIncome": Decimal("0"),
                "actualExpense": Decimal("0"),
                "estimatedIncome": Decimal("0"),
                "estimatedExpense": Decimal("0"),
                "actualCashInflow": Decimal("0"),
                "actualCashOutflow": Decimal("0"),
                "forecastCashInflow": Decimal("0"),
                "forecastCashOutflow": Decimal("0"),
            },
        )
        identity = (row.source_type, row.source_id, row.category)
        superseded_estimate = row.value_type == "estimated" and identity in actual_keys

        if row.profit_effect and not superseded_estimate:
            key = ("estimated" if row.value_type == "estimated" else "actual") + (
                "Income" if row.direction == "income" else "Expense"
            )
            bucket[key] += _decimal(row.amount)

        if row.cash_effect:
            cash_direction = "Inflow" if row.direction == "income" else "Outflow"
            settled_cash = row.value_type == "actual" and row.settlement_status in {"settled", "closed"}
            if settled_cash:
                bucket[f"actualCash{cash_direction}"] += _decimal(row.amount)
            elif not superseded_estimate:
                # 未结算的实际金额和仍有效的预计金额都属于未来现金需求/流入。
                bucket[f"forecastCash{cash_direction}"] += _decimal(row.amount)

        scope = scopes.setdefault(row.business_scope, {"count": 0, "estimated": 0, "pending": 0})
        scope["count"] += 1
        if row.value_type == "estimated" and not superseded_estimate:
            scope["estimated"] += 1
            todo["estimated"] += 1
        if row.settlement_status not in {"settled", "closed"}:
            scope["pending"] += 1
            todo["pendingSettlement"] += 1
        if row.settlement_status == "pending_confirmation":
            todo["pendingConfirmation"] += 1
        if row.invoice_status in {"missing", "pending"}:
            todo["pendingInvoice"] += 1
        if row.settlement_status == "anomaly":
            todo["anomalies"] += 1

    totals_by_currency = []
    for currency, values in sorted(totals.items()):
        actual_profit = values["actualIncome"] - values["actualExpense"]
        estimated_profit = (
            values["actualIncome"]
            + values["estimatedIncome"]
            - values["actualExpense"]
            - values["estimatedExpense"]
        )
        totals_by_currency.append({
            "currency": currency,
            **{key: str(value) for key, value in values.items()},
            "actualProfit": str(actual_profit),
            "estimatedProfit": str(estimated_profit),
            "actualNetCash": str(values["actualCashInflow"] - values["actualCashOutflow"]),
            "forecastNetCash": str(
                values["actualCashInflow"] + values["forecastCashInflow"]
                - values["actualCashOutflow"] - values["forecastCashOutflow"]
            ),
        })

    return {
        "entity": entity_dict(entity),
        "businessScope": business_scope,
        "year": year,
        "month": month,
        "entryCount": len(rows),
        "todo": todo,
        "totalsByCurrency": totals_by_currency,
        "scopeSummary": scopes,
        "recentEntries": [entry_dict(row, entity) for row in rows[:20]],
        "principle": "公司主体为第一维度，业务范围为第二维度；不同币种不直接相加。",
    }


def save_entity(
    db: Session,
    *,
    entity_id: int | None,
    code: str,
    name: str,
    country_code: str,
    base_currency: str,
    tax_id: str,
    status: str,
    is_default: bool,
    business_scopes: list[str],
    note: str,
) -> FinanceLegalEntity:
    row = db.get(FinanceLegalEntity, entity_id) if entity_id else FinanceLegalEntity()
    if row is None:
        raise ValueError("公司主体不存在")
    if is_default:
        for other in db.scalars(select(FinanceLegalEntity).where(FinanceLegalEntity.is_default.is_(True))).all():
            other.is_default = False
    row.code = code.strip().upper()
    row.name = name.strip()
    row.country_code = country_code.strip().upper() or "CN"
    row.base_currency = base_currency.strip().upper() or "CNY"
    row.tax_id = tax_id.strip()
    row.status = status
    row.is_default = is_default
    row.business_scopes = [scope for scope in business_scopes if scope in {"domestic", "foreign_trade"}]
    row.note = note.strip()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def save_entry(
    db: Session,
    *,
    entry_id: int | None,
    legal_entity_id: int,
    business_scope: str,
    source_type: str,
    source_id: str,
    source_no: str,
    category: str,
    direction: str,
    currency: str,
    amount: str,
    tax_amount: str,
    value_type: str,
    settlement_status: str,
    invoice_status: str,
    accounting_year: int,
    accounting_month: int,
    occurred_at: datetime | None,
    note: str,
    cash_effect: bool = True,
    profit_effect: bool = True,
) -> FinanceEntry:
    if business_scope not in {"domestic", "foreign_trade"}:
        raise ValueError("业务范围必须是内销或外贸")
    if direction not in {"income", "expense"}:
        raise ValueError("财务方向必须是收入或支出")
    if value_type not in {"actual", "estimated"}:
        raise ValueError("金额口径必须是实际或预计")
    if not (1 <= accounting_month <= 12):
        raise ValueError("账期月份不正确")
    entity = resolve_entity(db, legal_entity_id)
    row = db.get(FinanceEntry, entry_id) if entry_id else FinanceEntry()
    if row is None:
        raise ValueError("财务事项不存在")
    row.legal_entity_id = entity.id
    row.business_scope = business_scope
    row.source_type = source_type.strip() or "manual"
    clean_source_id = source_id.strip()
    if not clean_source_id and row.source_type == "manual":
        clean_source_id = f"manual-{entry_id or uuid4().hex}"
    row.source_id = clean_source_id
    row.source_no = source_no.strip()
    row.category = category.strip() or "other"
    row.direction = direction
    row.cash_effect = bool(cash_effect)
    row.profit_effect = bool(profit_effect)
    row.currency = currency.strip().upper() or entity.base_currency
    row.amount = _decimal(amount)
    row.tax_amount = _decimal(tax_amount)
    row.value_type = value_type
    row.settlement_status = settlement_status
    row.invoice_status = invoice_status
    row.accounting_year = accounting_year
    row.accounting_month = accounting_month
    row.occurred_at = occurred_at
    row.note = note.strip()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
