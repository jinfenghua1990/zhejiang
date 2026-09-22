from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.models.bank import BankAccount, BankTransaction
from app.models.payment import ReconciliationMatch
from app.models.tax import TaxInvoice, TaxInvoiceLink
from app.services import bank_summary_service as service


def test_bank_summary_groups_accounts_and_reconciliation_status(db_session):
    token = uuid4().hex[:10]
    account = BankAccount(
        account_no=f"BS-{token}",
        account_name="测试对公账户",
        bank_name="测试银行",
        currency="CNY",
        opening_balance=Decimal("1000.00"),
    )
    db_session.add(account)
    db_session.flush()

    income = BankTransaction(
        account_id=account.id,
        txn_date=date(2026, 8, 5),
        direction="in",
        amount=Decimal("500.00"),
        counterparty_name="平台回款",
        fingerprint=f"bs-income-{token}",
        raw={},
    )
    expense_matched = BankTransaction(
        account_id=account.id,
        txn_date=date(2026, 8, 10),
        direction="out",
        amount=Decimal("300.00"),
        counterparty_name="供应商甲",
        fingerprint=f"bs-expense-match-{token}",
        raw={},
    )
    expense_pending = BankTransaction(
        account_id=account.id,
        txn_date=date(2026, 8, 12),
        direction="out",
        amount=Decimal("100.00"),
        counterparty_name="供应商乙",
        fingerprint=f"bs-expense-pending-{token}",
        raw={},
    )
    previous_month = BankTransaction(
        account_id=account.id,
        txn_date=date(2026, 7, 31),
        direction="out",
        amount=Decimal("50.00"),
        counterparty_name="历史支出",
        fingerprint=f"bs-old-{token}",
        raw={},
    )
    db_session.add_all([income, expense_matched, expense_pending, previous_month])
    db_session.flush()

    db_session.add(ReconciliationMatch(
        txn_id=income.id,
        target_type="settlement",
        target_id=1,
        score=Decimal("1"),
        confidence="high",
        status="confirmed",
        matched_platform="测试",
        matched_by="manual",
    ))

    invoice = TaxInvoice(
        invoice_key=f"bs-inv-{token}",
        invoice_number=f"BS-INV-{token}",
        direction="input",
        status="issued",
        issue_date=datetime(2026, 8, 8, tzinfo=timezone.utc),
        seller_name="供应商甲",
        total_amount=Decimal("300.00"),
        source_system="pytest",
        raw={},
    )
    db_session.add(invoice)
    db_session.flush()
    db_session.add(TaxInvoiceLink(
        invoice_id=invoice.id,
        target_type="bank_transaction",
        target_id=expense_matched.id,
        allocated_amount=Decimal("300.00"),
        match_method="manual",
        confirmed=True,
    ))
    db_session.flush()

    result = service.build_summary(db_session, year=2026, month=8)

    assert result["summary"]["accountCount"] == 1
    assert Decimal(result["summary"]["monthIncome"]) == Decimal("500.00")
    assert Decimal(result["summary"]["monthExpense"]) == Decimal("400.00")
    assert Decimal(result["summary"]["monthNet"]) == Decimal("100.00")
    assert result["summary"]["monthTxnCount"] == 3
    assert result["summary"]["pendingCount"] == 1
    assert result["summary"]["pendingIncomeCount"] == 0
    assert result["summary"]["pendingExpenseCount"] == 1

    row = result["accounts"][0]
    assert row["accountNo"] == account.account_no
    assert Decimal(row["systemBalance"]) == Decimal("1050.00")
    assert Decimal(row["monthIncome"]) == Decimal("500.00")
    assert Decimal(row["monthExpense"]) == Decimal("400.00")
    assert row["pendingExpenseCount"] == 1
    assert row["lastTxnDate"] == "2026-08-12"
    assert row["balanceSource"] == "opening_plus_imported_transactions"


def test_partial_expense_invoice_link_stays_pending(db_session):
    token = uuid4().hex[:10]
    account = BankAccount(
        account_no=f"BS-PART-{token}",
        account_name="部分核对测试账户",
        bank_name="测试银行",
        currency="CNY",
        opening_balance=Decimal("0"),
    )
    db_session.add(account)
    db_session.flush()

    expense = BankTransaction(
        account_id=account.id,
        txn_date=date(2026, 8, 15),
        direction="out",
        amount=Decimal("5000.00"),
        counterparty_name="部分核对供应商",
        fingerprint=f"bs-partial-expense-{token}",
        raw={},
    )
    invoice = TaxInvoice(
        invoice_key=f"bs-partial-inv-{token}",
        invoice_number=f"BS-PART-INV-{token}",
        direction="input",
        status="issued",
        issue_date=datetime(2026, 8, 14, tzinfo=timezone.utc),
        seller_name="部分核对供应商",
        total_amount=Decimal("1000.00"),
        source_system="pytest",
        raw={},
    )
    db_session.add_all([expense, invoice])
    db_session.flush()
    db_session.add(TaxInvoiceLink(
        invoice_id=invoice.id,
        target_type="bank_transaction",
        target_id=expense.id,
        allocated_amount=Decimal("1000.00"),
        match_method="manual",
        confirmed=True,
    ))
    db_session.commit()

    result = service.build_summary(db_session, year=2026, month=8)
    assert result["summary"]["pendingExpenseCount"] == 1
    assert result["summary"]["pendingCount"] == 1
    assert result["accounts"][0]["pendingExpenseCount"] == 1
