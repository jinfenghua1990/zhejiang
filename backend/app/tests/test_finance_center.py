from decimal import Decimal

from app.models.finance import FinanceEntry
from app.services import finance_center_service as service


def test_default_legal_entity_is_available(db_session):
    entities = service.list_entities(db_session)

    assert entities
    default = next(item for item in entities if item["isDefault"])
    assert default["code"] == "ZJCB"
    assert default["name"] == "浙江柴本网络科技有限公司"
    assert default["baseCurrency"] == "CNY"


def test_finance_center_separates_scope_and_currency(db_session):
    entity = service.resolve_entity(db_session)

    domestic = FinanceEntry(
        legal_entity_id=entity.id,
        business_scope="domestic",
        source_type="pytest",
        source_id="domestic-1",
        source_no="D-1",
        category="sales_income",
        direction="income",
        currency="CNY",
        amount=Decimal("1000"),
        tax_amount=Decimal("0"),
        value_type="actual",
        settlement_status="settled",
        invoice_status="received",
        accounting_year=2026,
        accounting_month=9,
    )
    foreign = FinanceEntry(
        legal_entity_id=entity.id,
        business_scope="foreign_trade",
        source_type="pytest",
        source_id="foreign-1",
        source_no="EXP-1",
        category="export_tax_refund",
        direction="income",
        currency="CNY",
        amount=Decimal("200"),
        tax_amount=Decimal("0"),
        value_type="estimated",
        settlement_status="pending",
        invoice_status="not_required",
        accounting_year=2026,
        accounting_month=9,
    )
    euro_expense = FinanceEntry(
        legal_entity_id=entity.id,
        business_scope="foreign_trade",
        source_type="pytest",
        source_id="foreign-2",
        source_no="EXP-1",
        category="international_freight",
        direction="expense",
        currency="EUR",
        amount=Decimal("300"),
        tax_amount=Decimal("0"),
        value_type="actual",
        settlement_status="pending",
        invoice_status="pending",
        accounting_year=2026,
        accounting_month=9,
    )
    db_session.add_all([domestic, foreign, euro_expense])
    db_session.flush()

    all_data = service.center_overview(
        db_session,
        legal_entity_id=entity.id,
        business_scope="all",
        year=2026,
        month=9,
    )
    totals = {row["currency"]: row for row in all_data["totalsByCurrency"]}

    assert all_data["entryCount"] == 3
    assert totals["CNY"]["actualIncome"] == "1000"
    assert totals["CNY"]["estimatedIncome"] == "200"
    assert totals["EUR"]["actualExpense"] == "300"
    assert all_data["scopeSummary"]["domestic"]["count"] == 1
    assert all_data["scopeSummary"]["foreign_trade"]["count"] == 2
    assert all_data["todo"]["pendingSettlement"] == 2
    assert all_data["todo"]["pendingInvoice"] == 1

    foreign_data = service.center_overview(
        db_session,
        legal_entity_id=entity.id,
        business_scope="foreign_trade",
        year=2026,
        month=9,
    )
    assert foreign_data["entryCount"] == 2
    assert foreign_data["scopeSummary"]["domestic"]["count"] == 0


def test_manual_entries_receive_unique_source_ids(db_session):
    entity = service.resolve_entity(db_session)

    first = service.save_entry(
        db_session,
        entry_id=None,
        legal_entity_id=entity.id,
        business_scope="domestic",
        source_type="manual",
        source_id="",
        source_no="",
        category="other",
        direction="expense",
        currency="CNY",
        amount="10",
        tax_amount="0",
        value_type="actual",
        settlement_status="pending",
        invoice_status="unknown",
        accounting_year=2026,
        accounting_month=9,
        occurred_at=None,
        note="first",
    )
    second = service.save_entry(
        db_session,
        entry_id=None,
        legal_entity_id=entity.id,
        business_scope="domestic",
        source_type="manual",
        source_id="",
        source_no="",
        category="other",
        direction="expense",
        currency="CNY",
        amount="20",
        tax_amount="0",
        value_type="actual",
        settlement_status="pending",
        invoice_status="unknown",
        accounting_year=2026,
        accounting_month=9,
        occurred_at=None,
        note="second",
    )

    assert first.source_id
    assert second.source_id
    assert first.source_id != second.source_id
