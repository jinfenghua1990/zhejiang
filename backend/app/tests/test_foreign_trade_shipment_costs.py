from decimal import Decimal

from app.models.foreign_trade import ForeignTradeShipment
from app.services.foreign_trade_service import shipment_costs


def test_shipment_costs_include_trade_defence_and_recoverable_vat():
    row = ForeignTradeShipment(
        shipment_no="EXP-001",
        quantity=Decimal("10"),
        declared_value=Decimal("10000"),
        freight_to_eu=Decimal("1000"),
        insurance=Decimal("100"),
        customs_rate=Decimal("6"),
        anti_dumping_rate=Decimal("62.1"),
        countervailing_rate=Decimal("17.2"),
        import_vat_rate=Decimal("20"),
        import_vat_recoverable=True,
        import_vat_additional_base=Decimal("0"),
        clearance_fee=Decimal("200"),
        port_fee=Decimal("300"),
        last_mile_fee=Decimal("400"),
        other_import_fee=Decimal("100"),
        export_purchase_cost_cny=Decimal("50000"),
        domestic_export_cost_cny=Decimal("5000"),
        export_refund_base_cny=Decimal("50000"),
        export_refund_rate=Decimal("13"),
        actual_export_refund_cny=Decimal("0"),
    )

    costs = shipment_costs(row)

    cif = Decimal("11100")
    customs = cif * Decimal("0.06")
    anti_dumping = cif * Decimal("0.621")
    countervailing = cif * Decimal("0.172")
    vat_base = cif + customs + anti_dumping + countervailing
    vat = vat_base * Decimal("0.20")
    ancillary = Decimal("1000")
    landed_cash = cif + customs + anti_dumping + countervailing + vat + ancillary
    landed_ex_vat = landed_cash - vat

    assert Decimal(costs["cifValue"]) == cif
    assert Decimal(costs["customsDuty"]) == customs
    assert Decimal(costs["antiDumpingDuty"]) == anti_dumping
    assert Decimal(costs["countervailingDuty"]) == countervailing
    assert Decimal(costs["importVat"]) == vat
    assert Decimal(costs["landedCashRequirement"]) == landed_cash
    assert Decimal(costs["landedCostExRecoverableVat"]) == landed_ex_vat
    assert Decimal(costs["perUnitLandedCostExRecoverableVat"]) == landed_ex_vat / Decimal("10")
    assert Decimal(costs["estimatedExportRefundCny"]) == Decimal("6500")
    assert Decimal(costs["chinaNetCostEstimatedCny"]) == Decimal("48500")


def test_nonrecoverable_import_vat_stays_in_landed_cost():
    row = ForeignTradeShipment(
        shipment_no="EXP-002",
        quantity=Decimal("1"),
        declared_value=Decimal("1000"),
        freight_to_eu=Decimal("0"),
        insurance=Decimal("0"),
        customs_rate=Decimal("0"),
        anti_dumping_rate=Decimal("0"),
        countervailing_rate=Decimal("0"),
        import_vat_rate=Decimal("20"),
        import_vat_recoverable=False,
    )

    costs = shipment_costs(row)

    assert Decimal(costs["importVat"]) == Decimal("200")
    assert Decimal(costs["landedCashRequirement"]) == Decimal("1200")
    assert Decimal(costs["landedCostExRecoverableVat"]) == Decimal("1200")
