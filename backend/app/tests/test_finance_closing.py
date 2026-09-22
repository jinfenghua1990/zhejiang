from decimal import Decimal

from app.models.finance import FinanceEntry, FinanceLegalEntity
from app.services import finance_closing_service


def test_foreign_closing_profit_excludes_cash_only_import_vat(db_session):
    entity = FinanceLegalEntity(
        code="AT-CLOSE",
        name="Austria Closing GmbH",
        country_code="AT",
        base_currency="EUR",
        status="active",
        is_default=False,
        business_scopes=["foreign_trade"],
    )
    db_session.add(entity)
    db_session.flush()

    db_session.add_all([
        FinanceEntry(
            legal_entity_id=entity.id,
            business_scope="foreign_trade",
            source_type="foreign_order",
            source_id="1",
            source_no="AT-001",
            category="sales_income",
            direction="income",
            cash_effect=True,
            profit_effect=True,
            currency="EUR",
            amount=Decimal("1000.10"),
            tax_amount=Decimal("0"),
            value_type="actual",
            settlement_status="settled",
            invoice_status="unknown",
            accounting_year=2026,
            accounting_month=9,
        ),
        FinanceEntry(
            legal_entity_id=entity.id,
            business_scope="foreign_trade",
            source_type="foreign_shipment",
            source_id="1",
            source_no="EXP-001",
            category="anti_dumping_duty",
            direction="expense",
            cash_effect=True,
            profit_effect=True,
            currency="EUR",
            amount=Decimal("200"),
            tax_amount=Decimal("0"),
            value_type="actual",
            settlement_status="pending",
            invoice_status="not_required",
            accounting_year=2026,
            accounting_month=9,
        ),
        FinanceEntry(
            legal_entity_id=entity.id,
            business_scope="foreign_trade",
            source_type="foreign_shipment",
            source_id="1",
            source_no="EXP-001",
            category="import_vat",
            direction="expense",
            cash_effect=True,
            profit_effect=False,
            currency="EUR",
            amount=Decimal("160"),
            tax_amount=Decimal("0"),
            value_type="actual",
            settlement_status="pending",
            invoice_status="not_required",
            accounting_year=2026,
            accounting_month=9,
        ),
    ])
    db_session.flush()

    report = finance_closing_service.foreign_trade_summary(
        db_session,
        legal_entity_id=entity.id,
        year=2026,
        month=9,
    )
    eur = report["totalsByCurrency"][0]

    assert report["rowCount"] == 3
    assert eur["income"] == 1000.1
    assert eur["expense"] == 360.0
    assert eur["profit"] == 800.1
    assert eur["netCash"] == 640.1

    payload = finance_closing_service.foreign_trade_xlsx(
        db_session,
        legal_entity_id=entity.id,
        year=2026,
        month=9,
    )
    assert payload is not None
    assert payload[:2] == b"PK"
