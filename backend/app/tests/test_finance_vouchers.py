from decimal import Decimal

from sqlalchemy import select

from app.models.finance import FinanceEntry
from app.models.finance_voucher import FinanceVoucher, FinanceVoucherLine
from app.services import finance_center_service
from app.services import finance_voucher_service


def _entry(entity_id: int, *, source_id: str, amount: str, currency: str = "CNY", value_type: str = "actual"):
    return FinanceEntry(
        legal_entity_id=entity_id,
        business_scope="domestic",
        source_type="pytest",
        source_id=source_id,
        source_no=source_id,
        category="purchase_cost",
        direction="expense",
        currency=currency,
        amount=Decimal(amount),
        tax_amount=Decimal("0"),
        value_type=value_type,
        settlement_status="pending",
        invoice_status="received",
        accounting_year=2026,
        accounting_month=9,
    )


def test_generate_vouchers_only_books_actual_items_and_keeps_currency(db_session):
    entity = finance_center_service.resolve_entity(db_session)
    db_session.add_all([
        _entry(entity.id, source_id="actual-cny", amount="100.00"),
        _entry(entity.id, source_id="forecast-cny", amount="200.00", value_type="estimated"),
        _entry(entity.id, source_id="actual-eur", amount="30.00", currency="eur"),
    ])
    db_session.flush()

    result = finance_voucher_service.generate_vouchers_for_period(
        db_session, legal_entity_id=entity.id, year=2026, month=9
    )

    assert result["generated"] == 2
    assert result["skipped"] == 1
    vouchers = db_session.scalars(
        select(FinanceVoucher).where(
            FinanceVoucher.legal_entity_id == entity.id,
            FinanceVoucher.accounting_year == 2026,
            FinanceVoucher.accounting_month == 9,
        ).order_by(FinanceVoucher.voucher_no)
    ).all()
    assert [voucher.currency for voucher in vouchers] == ["CNY", "EUR"]
    for voucher in vouchers:
        lines = db_session.scalars(
            select(FinanceVoucherLine).where(FinanceVoucherLine.voucher_id == voucher.id)
        ).all()
        assert sum(line.amount for line in lines if line.direction == "debit") == sum(
            line.amount for line in lines if line.direction == "credit"
        )


def test_regeneration_does_not_duplicate_posted_entries(db_session):
    entity = finance_center_service.resolve_entity(db_session)
    db_session.add(_entry(entity.id, source_id="posted-entry", amount="88.00"))
    db_session.flush()

    finance_voucher_service.generate_vouchers_for_period(
        db_session, legal_entity_id=entity.id, year=2026, month=9
    )
    voucher = db_session.scalar(select(FinanceVoucher))
    assert voucher is not None
    voucher.status = "posted"
    db_session.flush()

    result = finance_voucher_service.generate_vouchers_for_period(
        db_session, legal_entity_id=entity.id, year=2026, month=9
    )

    assert result["generated"] == 0
    assert result["skipped"] == 1
    vouchers = db_session.scalars(select(FinanceVoucher)).all()
    assert len(vouchers) == 1
    assert vouchers[0].status == "posted"
