"""自动记账凭证生成引擎。

入口：generate_vouchers_for_period(db, legal_entity_id, year, month)

- 取该账期 finance_entries（已入账业务事项）
- 只把 actual 事项转成正式凭证，estimated 事项只用于预测
- 按 (category, direction) 匹配借贷模板，生成 FinanceVoucher + 多行 FinanceVoucherLine
- 每张凭证保留来源事项币种，避免把 EUR 等外币金额直接混入 CNY
- 每张凭证做借贷平衡断言（差额 > 0.01 报错并整段回滚），这是自动做账的正确性底线
- 幂等：重新生成时先清掉本账期 source=auto 的草稿凭证，已过账事项不重复入账
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import FinanceEntry, FinanceLegalEntity
from app.models.finance_voucher import FinanceVoucher, FinanceVoucherLine

ZERO = Decimal("0")
BALANCE_EPS = Decimal("0.01")

EXPENSE_ACCOUNTS = {
    "sales_cost": ("6401", "主营业务成本"),
    "purchase_cost": ("1405", "库存商品"),
    "inventory_purchase": ("1405", "库存商品"),
    "shipment_goods_cost": ("6401", "主营业务成本"),
    "cargo_insurance": ("6603", "财务费用-保险费"),
    "port_fee": ("6602", "管理费用-港杂费"),
    "platform_fee": ("6601", "销售费用-平台服务费"),
    "domestic_logistics": ("6601", "销售费用-物流费"),
    "international_freight": ("6601", "销售费用-国际运费"),
    "export_fee": ("6601", "销售费用-出口费用"),
    "customs_duty": ("6401", "主营业务成本-关税"),
    "anti_dumping_duty": ("6401", "主营业务成本-反倾销税"),
    "countervailing_duty": ("6401", "主营业务成本-反补贴税"),
    "import_vat": ("2221", "应交税费-应交增值税(进项)"),
    "clearance_fee": ("6602", "管理费用-清关费"),
    "last_mile_fee": ("6601", "销售费用-末端配送"),
    "refund": ("6051", "其他收益-退款"),
    "exchange_gain_loss": ("6603", "财务费用-汇兑损益"),
    "other": ("6602", "管理费用-其他"),
    "export_tax_refund": ("其他应收款", "应收出口退税款"),
}
INCOME_ACCOUNTS = {
    "sales_income": ("6001", "主营业务收入"),
    "other": ("6051", "其他业务收入"),
}

DEFAULT_CREDIT_LIABILITY = ("2202", "应付账款")
DEFAULT_DEBIT_ASSET = ("1002", "银行存款")


def _norm(v: Any) -> Decimal:
    if v is None:
        return ZERO
    try:
        return Decimal(str(v))
    except Exception:
        return ZERO


def build_lines(category: str, direction: str, amount: Decimal, tax_amount: Decimal) -> list[dict]:
    """按类别与方向产出借贷分录（amount 为含税总额）。"""
    amount = _norm(amount)
    tax_amount = _norm(tax_amount)
    net = amount - tax_amount
    if net < 0:
        net = amount
        tax_amount = ZERO
    lines: list[dict] = []
    if direction == "income":
        income_code, income_name = INCOME_ACCOUNTS.get(category, ("6001", "主营业务收入"))
        lines.append({"account_code": DEFAULT_DEBIT_ASSET[0], "account_name": DEFAULT_DEBIT_ASSET[1],
                      "direction": "debit", "amount": amount, "tax_amount": ZERO,
                      "summary": income_name + " 收款"})
        lines.append({"account_code": income_code, "account_name": income_name,
                      "direction": "credit", "amount": net, "tax_amount": ZERO,
                      "summary": income_name + "（不含税）"})
        if tax_amount > 0:
            lines.append({"account_code": "2221", "account_name": "应交税费-应交增值税(销项)",
                          "direction": "credit", "amount": tax_amount, "tax_amount": tax_amount,
                          "summary": "销项税额"})
    else:
        exp_code, exp_name = EXPENSE_ACCOUNTS.get(category, ("6602", "管理费用-其他"))
        lines.append({"account_code": exp_code, "account_name": exp_name,
                      "direction": "debit", "amount": net, "tax_amount": ZERO,
                      "summary": exp_name})
        if tax_amount > 0:
            lines.append({"account_code": "2221", "account_name": "应交税费-应交增值税(进项)",
                          "direction": "debit", "amount": tax_amount, "tax_amount": tax_amount,
                          "summary": "进项税额"})
        lines.append({"account_code": DEFAULT_CREDIT_LIABILITY[0],
                      "account_name": DEFAULT_CREDIT_LIABILITY[1],
                      "direction": "credit", "amount": amount, "tax_amount": ZERO,
                      "summary": "结转应付/银行存款"})
    return lines


def _assert_balanced(lines: list[FinanceVoucherLine], voucher_no: str) -> None:
    debit = sum((l.amount for l in lines if l.direction == "debit"), ZERO)
    credit = sum((l.amount for l in lines if l.direction == "credit"), ZERO)
    if abs(debit - credit) > BALANCE_EPS:
        raise ValueError(
            "凭证 " + voucher_no + " 借贷不平衡：借 " + str(debit) + " / 贷 " + str(credit)
        )


def generate_vouchers_for_period(
    db: Session, *, legal_entity_id: int, year: int, month: int, actor: str = "system"
) -> dict[str, Any]:
    entity = db.get(FinanceLegalEntity, legal_entity_id)
    if entity is None or entity.status == "archived":
        raise ValueError("公司主体不存在")

    posted_entry_ids = {
        entry_id
        for entry_id in db.scalars(
            select(FinanceVoucherLine.entry_id)
            .join(FinanceVoucher, FinanceVoucher.id == FinanceVoucherLine.voucher_id)
            .where(
                FinanceVoucher.legal_entity_id == legal_entity_id,
                FinanceVoucher.accounting_year == year,
                FinanceVoucher.accounting_month == month,
                FinanceVoucher.status == "posted",
                FinanceVoucherLine.entry_id.is_not(None),
            )
        ).all()
        if entry_id is not None
    }

    existing_numbers = db.scalars(
        select(FinanceVoucher.voucher_no).where(
            FinanceVoucher.legal_entity_id == legal_entity_id,
            FinanceVoucher.accounting_year == year,
            FinanceVoucher.accounting_month == month,
        )
    ).all()
    prefix = "记-" + str(year) + str(month).zfill(2) + "-"
    next_sequence = max(
        [int(number[len(prefix):]) for number in existing_numbers
         if number.startswith(prefix) and number[len(prefix):].isdigit()] or [0]
    )

    existing = db.scalars(
        select(FinanceVoucher).where(
            FinanceVoucher.legal_entity_id == legal_entity_id,
            FinanceVoucher.accounting_year == year,
            FinanceVoucher.accounting_month == month,
            FinanceVoucher.source == "auto",
            FinanceVoucher.status == "draft",
        )
    ).all()
    for v in existing:
        db.query(FinanceVoucherLine).filter(FinanceVoucherLine.voucher_id == v.id).delete()
        db.delete(v)
    db.flush()

    seq_counter = {"n": 0}
    entries = db.scalars(
        select(FinanceEntry).where(
            FinanceEntry.legal_entity_id == legal_entity_id,
            FinanceEntry.accounting_year == year,
            FinanceEntry.accounting_month == month,
        ).order_by(FinanceEntry.id)
    ).all()

    generated = 0
    skipped = 0
    errors: list[str] = []
    for entry in entries:
        # 预计事项只参与经营预测，不应进入正式记账凭证。
        if (entry.value_type or "actual") != "actual":
            skipped += 1
            continue
        # 已过账事项属于不可重复记账的历史事实，重新生成时保留原凭证。
        if entry.id in posted_entry_ids:
            skipped += 1
            continue
        amount = _norm(entry.amount)
        if amount == 0:
            skipped += 1
            continue
        spec = build_lines(entry.category, entry.direction, amount, _norm(entry.tax_amount))
        if not spec:
            skipped += 1
            continue
        seq_counter["n"] += 1
        sequence = next_sequence + seq_counter["n"]
        voucher_no = prefix + str(sequence).zfill(4)
        currency = (entry.currency or "CNY").strip().upper() or "CNY"
        voucher = FinanceVoucher(
            legal_entity_id=legal_entity_id,
            voucher_no=voucher_no,
            accounting_year=year,
            accounting_month=month,
            voucher_date=entry.occurred_at,
            currency=currency,
            source="auto",
            status="draft",
            note="auto from entry #" + str(entry.id) + " (" + entry.category + "/" + entry.direction + ")",
            raw={"entry_id": entry.id, "source_type": entry.source_type},
        )
        db.add(voucher)
        db.flush()
        lines = []
        for i, s in enumerate(spec, 1):
            line = FinanceVoucherLine(
                voucher_id=voucher.id,
                entry_id=entry.id,
                seq=i,
                account_code=s["account_code"],
                account_name=s["account_name"],
                direction=s["direction"],
                amount=s["amount"],
                tax_amount=s["tax_amount"],
                summary=s["summary"],
            )
            db.add(line)
            lines.append(line)
        try:
            _assert_balanced(lines, voucher_no)
        except ValueError as e:
            errors.append(str(e))
        generated += 1

    db.flush()
    if errors:
        raise ValueError("自动生成存在不平衡凭证：" + " | ".join(errors))

    return {
        "legalEntityId": legal_entity_id,
        "year": year,
        "month": month,
        "generated": generated,
        "skipped": skipped,
        "source": "auto",
    }
