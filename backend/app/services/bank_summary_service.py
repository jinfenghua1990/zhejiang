"""银行账户汇总。

财务中心的银行总览以“账户”为第一层、银行流水为事实来源。
本页只做只读聚合：
- 账户系统余额 = 期初余额 + 已导入全部收入 - 已导入全部支出
- 本期收入/支出/净流入按所选月份统计
- 收入是否已对账复用 reconciliation_matches confirmed
- 支出是否已对账按 tax_invoice_links(bank_transaction) 的确认分摊累计是否覆盖整笔付款判断

这里不把“系统余额”包装成银行实时余额；只有已导入流水才能参与计算。
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.bank import BankAccount, BankTransaction
from app.services import payment_invoice_match_service, reconciliation


def _dec(value: Decimal | float | int | str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _month_range(year: int, month: int) -> tuple[date, date]:
    if not (1 <= month <= 12):
        raise ValueError("月份必须在 1-12")
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def build_summary(db: Session, *, year: int, month: int) -> dict[str, Any]:
    start, end = _month_range(year, month)
    accounts = db.query(BankAccount).order_by(BankAccount.id).all()

    # 只把当月流水载入内存；全历史收支与最后交易日期用 SQL 聚合，避免整表读入。
    month_txns = (
        db.query(BankTransaction)
        .filter(BankTransaction.txn_date >= start, BankTransaction.txn_date <= end)
        .order_by(BankTransaction.txn_date, BankTransaction.id)
        .all()
    )
    income_by_account = dict(
        db.query(
            BankTransaction.account_id,
            func.coalesce(func.sum(BankTransaction.amount), 0),
        )
        .filter(BankTransaction.direction == "in")
        .group_by(BankTransaction.account_id)
        .all()
    )
    expense_by_account = dict(
        db.query(
            BankTransaction.account_id,
            func.coalesce(func.sum(BankTransaction.amount), 0),
        )
        .filter(BankTransaction.direction == "out")
        .group_by(BankTransaction.account_id)
        .all()
    )
    last_date_by_account = dict(
        db.query(
            BankTransaction.account_id,
            func.max(BankTransaction.txn_date),
        )
        .group_by(BankTransaction.account_id)
        .all()
    )

    confirmed_income_ids = reconciliation.confirmed_settlement_txn_ids(
        db, [row.id for row in month_txns if row.direction == "in"]
    )
    expense_ids = [row.id for row in month_txns if row.direction == "out"]
    confirmed_expense_ids = payment_invoice_match_service.fully_reconciled_txn_ids(db, expense_ids)

    account_ids = {row.id for row in accounts}
    month_by_account: dict[int | None, list[BankTransaction]] = defaultdict(list)
    for row in month_txns:
        key = row.account_id if row.account_id in account_ids else None
        month_by_account[key].append(row)

    def _bucket_total(
        agg: dict[int | None, Decimal], account_id: int | None
    ) -> Decimal:
        """取某账户的全历史聚合；None/孤儿 account_id 归入“未归属账户”桶。"""
        if account_id is not None:
            return _dec(agg.get(account_id))
        return _dec(
            sum(
                (amount for key, amount in agg.items() if key is None or key not in account_ids),
                Decimal("0"),
            )
        )

    rows: list[dict[str, Any]] = []
    # 历史脏数据可能没有 account_id 或指向已删除账户；保留一个“未归属账户”行，
    # 避免汇总静默丢金额。判定与旧的“按行归类”一致：任何未归属行都存在即显示该行。
    account_keys: list[int | None] = [row.id for row in accounts]
    has_unassigned = (
        None in month_by_account
        or any(key is None or key not in account_ids for key in income_by_account)
        or any(key is None or key not in account_ids for key in expense_by_account)
        or any(key is None or key not in account_ids for key in last_date_by_account)
    )
    if has_unassigned:
        account_keys.append(None)

    total_balance = Decimal("0")
    monthly_income = Decimal("0")
    monthly_expense = Decimal("0")
    pending_income = 0
    pending_expense = 0

    account_map = {row.id: row for row in accounts}
    for account_id in account_keys:
        account = account_map.get(account_id) if account_id is not None else None
        selected_rows = month_by_account.get(account_id, [])

        opening = _dec(account.opening_balance if account else None)
        all_income = _bucket_total(income_by_account, account_id)
        all_expense = _bucket_total(expense_by_account, account_id)
        system_balance = opening + all_income - all_expense

        income = sum((_dec(row.amount) for row in selected_rows if row.direction == "in"), Decimal("0"))
        expense = sum((_dec(row.amount) for row in selected_rows if row.direction == "out"), Decimal("0"))
        account_pending_income = sum(
            1 for row in selected_rows if row.direction == "in" and row.id not in confirmed_income_ids
        )
        account_pending_expense = sum(
            1 for row in selected_rows if row.direction == "out" and row.id not in confirmed_expense_ids
        )
        last_txn_date = last_date_by_account.get(account_id)

        total_balance += system_balance
        monthly_income += income
        monthly_expense += expense
        pending_income += account_pending_income
        pending_expense += account_pending_expense

        rows.append({
            "accountId": account.id if account else None,
            "accountNo": account.account_no if account else "",
            "accountCode": account.internal_code if account else "",
            "accountName": account.account_name if account else "未归属账户",
            "bankName": account.bank_name if account else "",
            "currency": account.currency if account else "CNY",
            "openingBalance": str(opening),
            "systemBalance": str(system_balance),
            "monthIncome": str(income),
            "monthExpense": str(expense),
            "monthNet": str(income - expense),
            "monthTxnCount": len(selected_rows),
            "pendingIncomeCount": account_pending_income,
            "pendingExpenseCount": account_pending_expense,
            "pendingCount": account_pending_income + account_pending_expense,
            "lastTxnDate": last_txn_date.isoformat() if last_txn_date else "",
            "balanceSource": "opening_plus_imported_transactions" if account and account.opening_balance is not None else "imported_transactions",
        })

    return {
        "year": year,
        "month": month,
        "summary": {
            "accountCount": len(account_keys),
            "systemBalance": str(total_balance),
            "monthIncome": str(monthly_income),
            "monthExpense": str(monthly_expense),
            "monthNet": str(monthly_income - monthly_expense),
            "monthTxnCount": len(month_txns),
            "pendingIncomeCount": pending_income,
            "pendingExpenseCount": pending_expense,
            "pendingCount": pending_income + pending_expense,
        },
        "accounts": rows,
    }
