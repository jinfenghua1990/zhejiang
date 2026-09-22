from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.models.bank import BankTransaction
from app.models.business_partner import (
    BusinessPartner,
    BusinessPartnerBankAccount,
    BusinessPartnerIdentifier,
    BusinessPartnerLink,
    BusinessPartnerRole,
)
from app.models.purchase import ExternalPurchaseOrder, Supplier
from app.models.tax import TaxInvoice
from app.services.partner_reference_service import (
    materialize_partner_references,
    partner_reference_coverage,
)


def _partner(db_session, name: str, tax_no: str = "") -> BusinessPartner:
    row = BusinessPartner(
        name=name,
        normalized_name=name,
        tax_no=tax_no,
        roles=["supplier", "counterparty"],
        status="active",
        bank_accounts=[],
    )
    db_session.add(row)
    db_session.flush()
    return row


def _invoice(db_session, seller: str, tax_no: str, amount: str = "100.00") -> TaxInvoice:
    row = TaxInvoice(
        invoice_key=f"phase1|{uuid4().hex}",
        invoice_number=f"P1-{uuid4().hex[:12]}",
        direction="input",
        status="issued",
        issue_date=datetime(2026, 9, 20, tzinfo=timezone.utc),
        seller_name=seller,
        seller_tax_id=tax_no,
        buyer_name="浙江柴本网络科技有限公司",
        total_amount=Decimal(amount),
        raw={},
    )
    db_session.add(row)
    db_session.flush()
    return row


def _txn(db_session, name: str, account: str, amount: str = "100.00") -> BankTransaction:
    row = BankTransaction(
        txn_date=date(2026, 9, 21),
        direction="out",
        amount=Decimal(amount),
        counterparty_name=name,
        counterparty_account=account,
        serial_no=f"P1-{uuid4().hex[:12]}",
        fingerprint=f"phase1|{uuid4().hex}",
        raw={},
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_materializes_confirmed_links_without_rewriting_raw_source_fields(db_session):
    partner = _partner(db_session, "统一主体甲", "91330000PHASE1001")
    purchase = ExternalPurchaseOrder(
        external_order_id=f"P1-PO-{uuid4().hex[:10]}",
        platform="other",
        supplier_name="采购侧旧名称",
        order_amount=Decimal("100.00"),
        paid_amount=Decimal("100.00"),
    )
    invoice = _invoice(db_session, "发票侧法人名称", "91330000PHASE1001")
    txn = _txn(db_session, "银行结算户", "6222-0000-1001")
    db_session.add(purchase)
    db_session.flush()

    original_names = (
        purchase.supplier_name,
        invoice.seller_name,
        txn.counterparty_name,
        txn.counterparty_account,
    )
    db_session.add_all(
        [
            BusinessPartnerLink(
                partner_id=partner.id,
                source_type="external_purchase_order",
                source_id=purchase.id,
                relation_role="supplier",
                raw_name=purchase.supplier_name,
                status="linked",
                match_method="manual",
                confidence=1.0,
                confirmed=True,
            ),
            BusinessPartnerLink(
                partner_id=partner.id,
                source_type="tax_invoice",
                source_id=invoice.id,
                relation_role="seller",
                raw_name=invoice.seller_name,
                raw_tax_no=invoice.seller_tax_id,
                status="linked",
                match_method="tax_no",
                confidence=1.0,
                confirmed=True,
            ),
            BusinessPartnerLink(
                partner_id=partner.id,
                source_type="bank_transaction",
                source_id=txn.id,
                relation_role="counterparty",
                raw_name=txn.counterparty_name,
                raw_account_no=txn.counterparty_account,
                status="linked",
                match_method="bank_account",
                confidence=1.0,
                confirmed=True,
            ),
        ]
    )
    db_session.flush()

    result = materialize_partner_references(db_session)

    assert result["materialized"] >= 3
    assert purchase.supplier_partner_id == partner.id
    assert invoice.seller_partner_id == partner.id
    assert txn.counterparty_partner_id == partner.id
    assert (
        purchase.supplier_name,
        invoice.seller_name,
        txn.counterparty_name,
        txn.counterparty_account,
    ) == original_names


def test_normalizes_roles_and_multiple_bank_accounts_into_relational_master(db_session):
    partner = _partner(db_session, "多账户主体", "91330000PHASE1002")
    partner.roles = ["supplier", "customer", "counterparty"]
    partner.bank_accounts = [
        {
            "bank_name": "银行A",
            "account_no": "6222 1000 0001",
            "account_name": "多账户主体",
            "is_primary": True,
        },
        {
            "bank_name": "银行B",
            "account_no": "7559-1000-0002",
            "account_name": "多账户主体结算户",
            "is_primary": False,
        },
    ]
    db_session.add(
        BusinessPartnerIdentifier(
            partner_id=partner.id,
            kind="bank_account",
            value="9558-1000-0003",
            normalized_value="955810000003",
            is_primary=False,
            source="manual",
        )
    )
    db_session.flush()

    first = materialize_partner_references(db_session)
    second = materialize_partner_references(db_session)

    roles = {
        row.role
        for row in db_session.query(BusinessPartnerRole)
        .filter(BusinessPartnerRole.partner_id == partner.id)
        .all()
    }
    accounts = (
        db_session.query(BusinessPartnerBankAccount)
        .filter(BusinessPartnerBankAccount.partner_id == partner.id)
        .order_by(BusinessPartnerBankAccount.normalized_account_no)
        .all()
    )

    assert roles == {"supplier", "customer", "counterparty"}
    assert {row.normalized_account_no for row in accounts} == {
        "622210000001",
        "755910000002",
        "955810000003",
    }
    assert sum(1 for row in accounts if row.is_primary) == 1
    assert first["rolesCreated"] == 3
    assert second["rolesCreated"] == 0


def test_supplier_profiles_can_share_one_canonical_partner(db_session):
    partner = _partner(db_session, "统一采购主体", "91330000PHASE1003")
    first = Supplier(
        partner_id=partner.id,
        name="1688店铺名",
        platform="1688",
        external_shop_id="SHOP-A",
    )
    second = Supplier(
        partner_id=partner.id,
        name="线下合同名",
        platform="线下",
        external_shop_id="SHOP-B",
    )
    db_session.add_all([first, second])
    db_session.flush()

    ids = [
        row.partner_id
        for row in db_session.query(Supplier)
        .filter(Supplier.partner_id == partner.id)
        .order_by(Supplier.id)
        .all()
    ]
    assert ids == [partner.id, partner.id]


def test_reference_coverage_reports_linked_and_unlinked_facts(db_session):
    partner = _partner(db_session, "覆盖率主体", "91330000PHASE1004")
    linked = ExternalPurchaseOrder(
        external_order_id=f"P1-COV-A-{uuid4().hex[:8]}",
        platform="other",
        supplier_name="覆盖率主体",
        supplier_partner_id=partner.id,
        order_amount=Decimal("10.00"),
    )
    unlinked = ExternalPurchaseOrder(
        external_order_id=f"P1-COV-B-{uuid4().hex[:8]}",
        platform="other",
        supplier_name="尚未归属供应商",
        order_amount=Decimal("20.00"),
    )
    db_session.add_all([linked, unlinked])
    db_session.flush()

    coverage = partner_reference_coverage(db_session)
    row = coverage["sources"]["externalPurchases"]

    assert row["total"] >= 2
    assert row["linked"] >= 1
    assert row["unlinked"] >= 1
    assert 0 <= row["coverage"] < 1
