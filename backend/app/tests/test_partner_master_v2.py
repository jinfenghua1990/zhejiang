from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.bank import BankTransaction
from app.models.business_partner import (
    BusinessPartner,
    BusinessPartnerBankAccount,
    BusinessPartnerIdentifier,
    BusinessPartnerLink,
)
from app.models.jky_web import JkyWebSalesOrder
from app.models.purchase import ExternalPurchaseOrder, Supplier
from app.models.sales import SalesOrder
from app.models.tax import TaxInvoice
from app.services import business_partner_service
from app.services.partner_reference_service import (
    materialize_partner_references,
    partner_reference_coverage,
)
from app.services.procurement_chain_service import supplier_summaries


def _partner(db_session, name: str, tax_no: str = "") -> BusinessPartner:
    row = BusinessPartner(
        name=name,
        normalized_name=business_partner_service.normalize_name(name),
        tax_no=business_partner_service.normalize_tax_no(tax_no),
        roles=["supplier"],
        status="active",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _invoice(db_session, seller: str, tax_no: str, amount: str = "100.00") -> TaxInvoice:
    row = TaxInvoice(
        invoice_key=f"partner-v2|{uuid4().hex}",
        invoice_number=f"PV2-{uuid4().hex[:12]}",
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
        serial_no=f"PV2-{uuid4().hex[:12]}",
        fingerprint=f"partner-v2|{uuid4().hex}",
        raw={},
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_materialize_partner_links_into_direct_operational_fks(db_session):
    partner = _partner(db_session, "统一主体甲", "91330000MASTER001")
    purchase = ExternalPurchaseOrder(
        external_order_id=f"PV2-PO-{uuid4().hex[:10]}",
        platform="other",
        supplier_name="采购侧旧名称",
        order_amount=Decimal("100.00"),
        paid_amount=Decimal("100.00"),
    )
    invoice = _invoice(db_session, "发票侧法人名称", "91330000MASTER001")
    txn = _txn(db_session, "银行结算户", "622200001001")
    db_session.add(purchase)
    db_session.flush()

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


def test_direct_partner_fk_is_authoritative_over_different_raw_name(db_session):
    partner = _partner(db_session, "标准法人主体", "91330000MASTER002")
    purchase = ExternalPurchaseOrder(
        external_order_id=f"PV2-DIRECT-{uuid4().hex[:10]}",
        platform="other",
        supplier_name="完全不同的店铺展示名",
        supplier_partner_id=partner.id,
        order_amount=Decimal("88.00"),
        paid_amount=Decimal("88.00"),
    )
    db_session.add(purchase)
    db_session.flush()

    business_partner_service.sync_business_partners(db_session)

    active = (
        db_session.query(BusinessPartner)
        .filter(BusinessPartner.status == "active")
        .all()
    )
    assert [row.id for row in active] == [partner.id]
    link = (
        db_session.query(BusinessPartnerLink)
        .filter_by(
            source_type="external_purchase_order",
            source_id=purchase.id,
            relation_role="supplier",
        )
        .one()
    )
    assert link.partner_id == partner.id
    assert link.match_method == "direct_partner_fk"


def test_supplier_summary_groups_different_raw_names_by_partner_id(db_session):
    partner = _partner(db_session, "合锦（广州）供应链有限公司", "91440101MA5AQUHB7W")
    db_session.add_all(
        [
            ExternalPurchaseOrder(
                external_order_id=f"PV2-HJ-A-{uuid4().hex[:8]}",
                platform="other",
                supplier_name="合锦供应链店铺",
                supplier_partner_id=partner.id,
                order_amount=Decimal("3080.00"),
                paid_amount=Decimal("3080.00"),
            ),
            ExternalPurchaseOrder(
                external_order_id=f"PV2-HJ-B-{uuid4().hex[:8]}",
                platform="other",
                supplier_name="合锦(广州)供应链有限公司",
                supplier_partner_id=partner.id,
                order_amount=Decimal("2310.00"),
                paid_amount=Decimal("2310.00"),
            ),
        ]
    )
    db_session.flush()

    result = supplier_summaries(db_session, limit=100, offset=0)

    rows = [row for row in result["items"] if row.get("partnerId") == partner.id]
    assert len(rows) == 1
    assert rows[0]["supplierName"] == "合锦（广州）供应链有限公司"
    assert rows[0]["orderCount"] == 2
    assert rows[0]["totalPurchase"] == 5390.0


def test_multiple_supplier_profiles_share_one_canonical_partner(db_session):
    partner = _partner(db_session, "多渠道统一供应商", "91330000MASTER003")
    db_session.add_all(
        [
            Supplier(
                partner_id=partner.id,
                name="1688店铺名",
                platform="1688",
                external_shop_id="SHOP-A",
            ),
            Supplier(
                partner_id=partner.id,
                name="线下合同名称",
                platform="线下",
                external_shop_id="SHOP-B",
            ),
        ]
    )
    db_session.flush()

    business_partner_service.sync_business_partners(db_session)

    active = (
        db_session.query(BusinessPartner)
        .filter(BusinessPartner.status == "active")
        .all()
    )
    assert [row.id for row in active] == [partner.id]
    profiles = db_session.query(Supplier).filter(Supplier.partner_id == partner.id).all()
    assert len(profiles) == 2

    detail = business_partner_service.partner_detail(db_session, partner.id)
    aliases = {row["value"] for row in detail["identifiers"] if row["kind"] == "alias"}
    assert "线下合同名称" in aliases or "1688店铺名" in aliases


def test_platform_and_customer_identifiers_are_stable_identity_keys(db_session):
    partner = _partner(db_session, "稳定业务账号主体")
    db_session.add_all(
        [
            BusinessPartnerIdentifier(
                partner_id=partner.id,
                kind="platform_account",
                value="1688:member-001",
                normalized_value=business_partner_service.normalize_identifier(
                    "platform_account", "1688:member-001"
                ),
                source="manual",
            ),
            BusinessPartnerIdentifier(
                partner_id=partner.id,
                kind="customer_code",
                value="CUS-001",
                normalized_value=business_partner_service.normalize_identifier(
                    "customer_code", "CUS-001"
                ),
                source="manual",
            ),
        ]
    )
    db_session.flush()

    ctx = business_partner_service.SyncContext(db_session)
    assert (
        ctx.index.resolve(
            name="完全不同的新店铺名称",
            platform_account="1688:member-001",
        ).partner.id
        == partner.id
    )
    assert ctx.index.resolve(customer_code="CUS-001").partner.id == partner.id


def test_duplicate_merge_repoints_all_direct_facts_and_archives_source(db_session):
    target = _partner(db_session, "合锦供应链")
    source = _partner(db_session, "合锦（广州）供应链有限公司", "91440101MA5AQUHB7W")
    profile = Supplier(
        partner_id=source.id,
        name="合锦1688店",
        platform="1688",
        tax_no="91440101MA5AQUHB7W",
    )
    purchase = ExternalPurchaseOrder(
        external_order_id=f"PV2-MERGE-{uuid4().hex[:10]}",
        platform="other",
        supplier_name="合锦1688店",
        supplier_partner_id=source.id,
        order_amount=Decimal("3080.00"),
        paid_amount=Decimal("3080.00"),
    )
    invoice = _invoice(db_session, source.name, source.tax_no, "3080.00")
    invoice.seller_partner_id = source.id
    txn = _txn(db_session, "合锦结算户", "622200003080", "3080.00")
    txn.counterparty_partner_id = source.id
    db_session.add_all([profile, purchase])
    db_session.flush()

    result = business_partner_service.decide_duplicate(
        db_session,
        target.id,
        other_partner_id=source.id,
        same=True,
        note="人工确认同一法人主体",
        actor="pytest",
    )

    assert result["mergeResult"]["targetPartnerId"] == target.id
    assert source.status == "archived"
    assert target.tax_no == "91440101MA5AQUHB7W"
    assert profile.partner_id == target.id
    assert purchase.supplier_partner_id == target.id
    assert invoice.seller_partner_id == target.id
    assert txn.counterparty_partner_id == target.id


def test_duplicate_merge_rejects_conflicting_tax_numbers(db_session):
    left = _partner(db_session, "税号冲突主体A", "91330000CONFLICTA")
    right = _partner(db_session, "税号冲突主体B", "91330000CONFLICTB")

    with pytest.raises(ValueError, match="税号冲突"):
        business_partner_service.decide_duplicate(
            db_session,
            left.id,
            other_partner_id=right.id,
            same=True,
            actor="pytest",
        )

    assert left.status == "active"
    assert right.status == "active"


def test_relational_bank_master_overrides_stale_legacy_json(db_session):
    partner = _partner(db_session, "银行主档测试")
    partner.bank_accounts = [
        {
            "bank_name": "旧银行",
            "account_no": "1111",
            "account_name": "旧户名",
            "is_primary": True,
        }
    ]
    db_session.add(
        BusinessPartnerBankAccount(
            partner_id=partner.id,
            bank_name="新银行",
            account_no="6222-9999",
            normalized_account_no="62229999",
            account_name="新户名",
            is_primary=True,
            status="active",
            source="manual",
            verified=True,
        )
    )
    db_session.flush()

    detail = business_partner_service.partner_detail(db_session, partner.id)

    assert detail is not None
    assert detail["bankAccounts"] == [
        {
            "bankName": "新银行",
            "accountNo": "62229999",
            "accountName": "新户名",
            "isPrimary": True,
        }
    ]


def test_partner_reference_coverage_reports_direct_linkage(db_session):
    partner = _partner(db_session, "覆盖率主体")
    purchase = ExternalPurchaseOrder(
        external_order_id=f"PV2-COVER-{uuid4().hex[:10]}",
        platform="other",
        supplier_name="覆盖率主体",
        supplier_partner_id=partner.id,
    )
    db_session.add(purchase)
    db_session.flush()

    coverage = partner_reference_coverage(db_session)

    assert coverage["sources"]["externalPurchases"]["total"] >= 1
    assert coverage["sources"]["externalPurchases"]["linked"] >= 1

def test_supplier_profile_update_cannot_overwrite_canonical_identity(client, db_session):
    partner = _partner(db_session, "统一主体不可被画像覆盖", "91330000CANON001")
    partner.contact = "主体联系人"
    partner.phone = "13800000001"
    partner.address = "主体地址"
    partner.notes = "主体通用备注"
    profile = Supplier(
        partner_id=partner.id,
        name="1688渠道旧名称",
        platform="1688",
        external_shop_id="SHOP-OLD",
        tax_no=partner.tax_no,
        contact=partner.contact,
        phone=partner.phone,
        address=partner.address,
        notes="旧采购备注",
    )
    db_session.add(profile)
    db_session.flush()

    response = client.put(
        f"/api/v1/suppliers/{profile.id}",
        json={
            "name": "错误尝试覆盖主体名称",
            "platform": "线下",
            "externalShopId": "SHOP-NEW",
            "contact": "错误联系人",
            "taxNo": "91330000WRONG999",
            "phone": "19999999999",
            "address": "错误地址",
            "notes": "新的采购画像备注",
            "isTemp": False,
        },
    )

    assert response.status_code == 200
    db_session.expire_all()
    partner_after = db_session.get(BusinessPartner, partner.id)
    profile_after = db_session.get(Supplier, profile.id)
    assert partner_after is not None
    assert profile_after is not None
    assert partner_after.name == "统一主体不可被画像覆盖"
    assert partner_after.tax_no == "91330000CANON001"
    assert partner_after.contact == "主体联系人"
    assert partner_after.phone == "13800000001"
    assert partner_after.address == "主体地址"
    assert partner_after.notes == "主体通用备注"
    assert profile_after.platform == "线下"
    assert profile_after.external_shop_id == "SHOP-NEW"
    assert profile_after.notes == "新的采购画像备注"
    assert profile_after.tax_no == "91330000CANON001"

    payload = response.json()
    assert payload["name"] == "统一主体不可被画像覆盖"
    assert payload["taxNo"] == "91330000CANON001"
    assert payload["notes"] == "新的采购画像备注"

def test_generic_sales_order_inherits_customer_partner_from_stable_jky_identity(db_session):
    partner = BusinessPartner(
        name="销售客户甲",
        normalized_name="销售客户甲",
        roles=["customer"],
        status="active",
    )
    db_session.add(partner)
    db_session.flush()

    jky = JkyWebSalesOrder(
        trade_no="TRADE-CANON-001",
        source_trade_no="PLATFORM-CANON-001",
        customer_code="CUST-001",
        customer_account="customer-a",
        customer_partner_id=partner.id,
        raw={"客户名称": "销售客户甲"},
    )
    sales = SalesOrder(
        order_no="LOCAL-SALES-001",
        source_provider="jky_file",
        source_order_id="TRADE-CANON-001",
        identity_keys=["PLATFORM-CANON-001"],
        raw={},
    )
    db_session.add_all([jky, sales])
    db_session.flush()

    result = materialize_partner_references(db_session)

    assert result["bySource"]["sales_order"] == 1
    assert sales.customer_partner_id == partner.id


def test_generic_sales_order_does_not_inherit_conflicting_customer_identity(db_session):
    first = BusinessPartner(
        name="销售客户冲突甲",
        normalized_name="销售客户冲突甲",
        roles=["customer"],
        status="active",
    )
    second = BusinessPartner(
        name="销售客户冲突乙",
        normalized_name="销售客户冲突乙",
        roles=["customer"],
        status="active",
    )
    db_session.add_all([first, second])
    db_session.flush()
    db_session.add_all([
        JkyWebSalesOrder(
            trade_no="TRADE-CONFLICT-A",
            source_trade_no="SHARED-SOURCE-ORDER",
            customer_partner_id=first.id,
            raw={"客户名称": "销售客户冲突甲"},
        ),
        JkyWebSalesOrder(
            trade_no="TRADE-CONFLICT-B",
            source_trade_no="SHARED-SOURCE-ORDER",
            customer_partner_id=second.id,
            raw={"客户名称": "销售客户冲突乙"},
        ),
    ])
    sales = SalesOrder(
        order_no="SHARED-SOURCE-ORDER",
        source_provider="legacy",
        source_order_id="",
        raw={},
    )
    db_session.add(sales)
    db_session.flush()

    result = materialize_partner_references(db_session)

    assert result["bySource"].get("sales_order", 0) == 0
    assert sales.customer_partner_id is None

