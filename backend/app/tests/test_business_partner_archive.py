from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.models.bank import BankTransaction
from app.models.business_partner import BusinessPartnerLink
from app.models.purchase import ExternalPurchaseOrder, JackyunPurchaseOrder, JackyunPurchaseOrderLink, Supplier
from app.models.tax import TaxInvoice, TaxInvoiceLink
from app.services import business_partner_service as service


def _invoice(db_session, *, seller: str, tax_no: str = "", amount: str = "100.00") -> TaxInvoice:
    row = TaxInvoice(
        invoice_key=f"partner-archive|{uuid4().hex}",
        invoice_number=f"PA-{uuid4().hex[:12]}",
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


def _bank_txn(db_session, *, name: str, account: str = "", amount: str = "100.00") -> BankTransaction:
    row = BankTransaction(
        txn_date=date(2026, 9, 21),
        direction="out",
        amount=Decimal(amount),
        counterparty_name=name,
        counterparty_account=account,
        serial_no=f"PA-{uuid4().hex[:12]}",
        fingerprint=f"partner-archive|{uuid4().hex}",
        raw={"fields": {"对方户名": name, "对方账号": account}},
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_partner_archive_links_purchase_invoice_and_bank_by_canonical_identity(db_session):
    supplier = Supplier(
        name="归档供应商甲",
        tax_no="91330100PARTNER001",
        bank_account_no="6222 0000 0001",
        bank_account_name="归档供应商甲",
    )
    purchase = ExternalPurchaseOrder(
        external_order_id=f"PA-PO-{uuid4().hex[:10]}",
        platform="1688",
        supplier_name="归档供应商甲",
        order_amount=Decimal("100.00"),
        paid_amount=Decimal("100.00"),
    )
    invoice = _invoice(db_session, seller="归档供应商甲", tax_no="91330100PARTNER001")
    txn = _bank_txn(db_session, name="归档供应商甲", account="622200000001")
    db_session.add_all([supplier, purchase])
    db_session.flush()

    service.sync_business_partners(db_session)
    detail_rows = service.list_partners(db_session, keyword="归档供应商甲")["items"]
    assert len(detail_rows) == 1
    partner_id = detail_rows[0]["id"]
    detail = service.partner_detail(db_session, partner_id)
    assert detail is not None
    assert detail["summary"]["purchaseOrderCount"] == 1
    assert detail["summary"]["invoiceCount"] == 1
    assert detail["summary"]["bankTransactionCount"] == 1
    assert detail["summary"]["bankPaidAmount"] == 100.0
    assert detail["taxNo"] == "91330100PARTNER001"
    assert {row["id"] for row in detail["invoices"]} == {invoice.id}
    assert {row["id"] for row in detail["payments"]} == {txn.id}


def test_similar_legal_name_needs_manual_confirmation_then_becomes_alias(db_session):
    supplier = Supplier(name="义乌市档案注塑厂")
    purchase = ExternalPurchaseOrder(
        external_order_id=f"PA-SAFE-{uuid4().hex[:10]}",
        platform="1688",
        supplier_name="义乌市档案注塑厂",
        order_amount=Decimal("241.92"),
        paid_amount=Decimal("241.92"),
    )
    db_session.add_all([supplier, purchase])
    db_session.flush()
    invoice = _invoice(
        db_session,
        seller="义乌市档案注塑厂（个体工商户）",
        tax_no="92330782PARTNER001",
        amount="241.92",
    )

    service.sync_business_partners(db_session)
    partner = service.list_partners(db_session, keyword="义乌市档案注塑厂")["items"][0]
    detail = service.partner_detail(db_session, partner["id"])
    assert detail is not None
    assert detail["summary"]["invoiceCount"] == 0
    assert len(detail["reviewItems"]) == 1
    review = detail["reviewItems"][0]
    assert review["sourceType"] == "tax_invoice"
    assert review["sourceId"] == invoice.id

    service.claim_review_link(
        db_session,
        partner_id=partner["id"],
        link_id=review["linkId"],
        note="人工确认同一供应商主体",
    )
    detail = service.partner_detail(db_session, partner["id"])
    assert detail is not None
    assert detail["summary"]["invoiceCount"] == 1
    aliases = [item["value"] for item in detail["identifiers"] if item["kind"] == "alias"]
    assert "义乌市档案注塑厂（个体工商户）" in aliases


def test_same_entity_two_supplier_records_are_flagged_not_merged(db_session):
    """同一主体的两条供应商记录不自动合并，但双方都要给出“疑似同一主体”提示。"""
    plain = Supplier(name="义乌市聚科注塑厂")
    licensed = Supplier(name="义乌市聚科注塑厂（个体工商户）", tax_no="92330782MAEBFPHX0W")
    db_session.add_all([plain, licensed])
    db_session.flush()

    service.sync_business_partners(db_session)

    items = service.list_partners(db_session, keyword="聚科")["items"]
    assert len(items) == 2
    assert {row["possibleDuplicateCount"] for row in items} == {1}

    partner_ids = {row["id"] for row in items}
    for partner_id in partner_ids:
        detail = service.partner_detail(db_session, partner_id)
        assert detail is not None
        other_ids = {item["id"] for item in detail["possibleDuplicates"]}
        assert other_ids == partner_ids - {partner_id}
        assert detail["possibleDuplicates"][0]["taxNo"] in ("", "92330782MAEBFPHX0W")


def test_confirmed_invoice_payment_relation_connects_bank_when_names_differ(db_session):
    supplier = Supplier(name="付款关联供应商", tax_no="91330100PARTNER002")
    db_session.add(supplier)
    db_session.flush()
    invoice = _invoice(db_session, seller="付款关联供应商", tax_no="91330100PARTNER002", amount="3080.00")
    txn = _bank_txn(db_session, name="代付结算主体", account="666576427385", amount="3080.00")
    db_session.add(
        TaxInvoiceLink(
            invoice_id=invoice.id,
            target_type="bank_transaction",
            target_id=txn.id,
            allocated_amount=Decimal("3080.00"),
            match_method="manual",
            confirmed=True,
        )
    )
    db_session.flush()

    service.sync_business_partners(db_session)
    partner = service.list_partners(db_session, keyword="付款关联供应商")["items"][0]
    detail = service.partner_detail(db_session, partner["id"])
    assert detail is not None
    assert detail["summary"]["bankTransactionCount"] == 1
    assert detail["payments"][0]["counterpartyName"] == "代付结算主体"
    link = db_session.query(BusinessPartnerLink).filter_by(
        source_type="bank_transaction", source_id=txn.id, relation_role="counterparty"
    ).one()
    assert link.partner_id == partner["id"]
    assert link.match_method == "invoice_payment_link"


def test_bank_row_without_counterparty_identity_is_skipped(db_session):
    """利息、手续费等没有对方户名和账号的流水不能建档，也不能让同步报错。"""
    txn = _bank_txn(db_session, name="", account="", amount="1.26")
    txn.summary = "利息"
    db_session.flush()

    service.sync_business_partners(db_session)

    assert service.list_partners(db_session)["total"] == 0
    assert (
        db_session.query(BusinessPartnerLink)
        .filter_by(source_type="bank_transaction", source_id=txn.id)
        .count()
        == 0
    )


def _duplicate_pair(db_session, *, plain_name: str, licensed_name: str, tax_no: str) -> dict[str, int]:
    plain = Supplier(name=plain_name)
    licensed = Supplier(name=licensed_name, tax_no=tax_no)
    db_session.add_all([plain, licensed])
    db_session.flush()
    service.sync_business_partners(db_session)
    items = service.list_partners(db_session, keyword=plain_name)["items"]
    assert len(items) == 2
    assert {row["possibleDuplicateCount"] for row in items} == {1}
    return {row["name"]: row["id"] for row in items}


def test_duplicate_decision_different_clears_suggestion_for_both_partners(db_session):
    ids = _duplicate_pair(
        db_session,
        plain_name="义乌市庚注塑厂",
        licensed_name="义乌市庚注塑厂（个体工商户）",
        tax_no="92330782MAEBFPHX0A",
    )
    plain_id, licensed_id = ids["义乌市庚注塑厂"], ids["义乌市庚注塑厂（个体工商户）"]

    detail = service.decide_duplicate(
        db_session, plain_id, other_partner_id=licensed_id, same=False, actor="财务甲"
    )
    assert detail["possibleDuplicates"] == []
    assert detail["formerNames"] == []
    other = service.partner_detail(db_session, licensed_id)
    assert other is not None
    assert other["possibleDuplicates"] == []
    assert {row["possibleDuplicateCount"] for row in service.list_partners(db_session, keyword="庚注塑厂")["items"]} == {0}


def test_duplicate_decision_same_merges_into_one_canonical_partner(db_session):
    ids = _duplicate_pair(
        db_session,
        plain_name="义乌市辛注塑厂",
        licensed_name="义乌市辛注塑厂（个体工商户）",
        tax_no="92330782MAEBFPHX0B",
    )
    plain_id, licensed_id = ids["义乌市辛注塑厂"], ids["义乌市辛注塑厂（个体工商户）"]

    detail = service.decide_duplicate(
        db_session,
        plain_id,
        other_partner_id=licensed_id,
        same=True,
        note="人工确认同一主体",
        actor="财务甲",
    )
    assert detail["possibleDuplicates"] == []
    assert detail["formerNames"] == ["义乌市辛注塑厂（个体工商户）"]
    assert detail["taxNo"] == "92330782MAEBFPHX0B"
    assert detail["mergeResult"]["targetPartnerId"] == plain_id
    assert detail["mergeResult"]["archivedPartnerId"] == licensed_id

    # 合并后只能剩一个活跃主体；原主体仅以 archived 审计壳保留。
    active = service.list_partners(db_session, keyword="辛注塑厂")["items"]
    assert [row["id"] for row in active] == [plain_id]
    archived = db_session.get(service.BusinessPartner, licensed_id)
    assert archived is not None
    assert archived.status == "archived"

    # 后到的旧法人名称发票必须直接落到保留主体，不再分叉出第二个主体。
    invoice = _invoice(
        db_session,
        seller="义乌市辛注塑厂（个体工商户）",
        tax_no="92330782MAEBFPHX0B",
        amount="66.00",
    )
    service.sync_business_partners(db_session)
    link = (
        db_session.query(BusinessPartnerLink)
        .filter_by(source_type="tax_invoice", source_id=invoice.id, relation_role="seller")
        .one()
    )
    assert link.partner_id == plain_id
    assert link.status == "linked"
    assert invoice.seller_partner_id == plain_id


def test_duplicate_decision_same_keeps_plain_name_sources_linked(db_session):
    """登记曾用名后规范名称仍优先：无税号的旧名来源不会变成多命中待确认。"""
    ids = _duplicate_pair(
        db_session,
        plain_name="义乌市壬注塑厂",
        licensed_name="义乌市壬注塑厂（个体工商户）",
        tax_no="92330782MAEBFPHX0C",
    )
    plain_id, licensed_id = ids["义乌市壬注塑厂"], ids["义乌市壬注塑厂（个体工商户）"]
    purchase = ExternalPurchaseOrder(
        external_order_id=f"PA-DUP-{uuid4().hex[:10]}",
        platform="1688",
        supplier_name="义乌市壬注塑厂",
        order_amount=Decimal("120.00"),
        paid_amount=Decimal("120.00"),
    )
    db_session.add(purchase)
    db_session.flush()

    service.decide_duplicate(db_session, plain_id, other_partner_id=licensed_id, same=True)
    service.sync_business_partners(db_session)

    link = (
        db_session.query(BusinessPartnerLink)
        .filter_by(source_type="external_purchase_order", source_id=purchase.id, relation_role="supplier")
        .one()
    )
    assert link.status == "linked"
    assert link.partner_id == plain_id
    detail = service.partner_detail(db_session, plain_id)
    assert detail is not None
    assert detail["reviewItems"] == []
    assert detail["summary"]["purchaseOrderCount"] == 1



def test_partner_sync_endpoint_also_runs_bank_invoice_reconciliation(client, db_session):
    """回归：核对全部来源不能只归档发票/流水，还必须建立两者之间的付款关联。"""
    name = "合锦（广州）供应链有限公司"
    invoice = _invoice(db_session, seller=name, amount="3080.00")
    txn = _bank_txn(db_session, name=name, amount="3080.00")
    invoice.issue_date = datetime(2026, 7, 24, tzinfo=timezone.utc)
    txn.txn_date = date(2026, 7, 22)
    db_session.commit()

    response = client.post("/api/v1/finance/partners/sync")
    assert response.status_code == 200
    payload = response.json()
    assert payload["bankInvoicePeriods"] >= 1
    assert payload["bankInvoiceMatchesCreated"] >= 1

    link = db_session.query(TaxInvoiceLink).filter_by(
        invoice_id=invoice.id,
        target_type="bank_transaction",
        target_id=txn.id,
    ).one()
    assert link.confirmed is True
    assert link.allocated_amount == Decimal("3080.0000")

    partner = service.list_partners(db_session, keyword=name)["items"][0]
    detail = service.partner_detail(db_session, partner["id"])
    assert detail is not None
    invoice_row = next(row for row in detail["invoices"] if row["id"] == invoice.id)
    assert invoice_row["bankPaidAmount"] == 3080.0
    assert invoice_row["bankRemainingAmount"] == 0.0



def test_partner_master_supports_former_names_and_multiple_bank_accounts(db_session):
    partner = service.create_partner(
        db_session,
        {
            "name": "多账户测试供应商",
            "tax_no": "91330000MULTIBANK01",
            "roles": ["supplier"],
            "former_names": ["多账户测试供应商（旧）", "多账户供应商老名称"],
            "bank_accounts": [
                {
                    "bank_name": "中国银行杭州支行",
                    "account_no": "6222 0000 0001",
                    "account_name": "多账户测试供应商",
                    "is_primary": True,
                },
                {
                    "bank_name": "招商银行杭州分行",
                    "account_no": "7559-0000-0002",
                    "account_name": "多账户测试供应商结算户",
                    "is_primary": False,
                },
            ],
        },
    )
    db_session.flush()

    detail = service.partner_detail(db_session, partner.id)
    assert detail is not None
    assert detail["formerNames"] == ["多账户测试供应商（旧）", "多账户供应商老名称"]
    assert detail["bankAccountNo"] == "622200000001"
    assert detail["bankName"] == "中国银行杭州支行"
    assert detail["bankAccounts"] == [
        {
            "bankName": "中国银行杭州支行",
            "accountNo": "622200000001",
            "accountName": "多账户测试供应商",
            "isPrimary": True,
        },
        {
            "bankName": "招商银行杭州分行",
            "accountNo": "755900000002",
            "accountName": "多账户测试供应商结算户",
            "isPrimary": False,
        },
    ]

    identifiers = {(row["kind"], row["value"]) for row in detail["identifiers"]}
    assert ("former_name", "多账户测试供应商（旧）") in identifiers
    assert ("former_name", "多账户供应商老名称") in identifiers
    assert ("bank_account", "622200000001") in identifiers
    assert ("bank_account", "755900000002") in identifiers

    # 任一维护过的账号都必须能反查回同一个往来单位，用于银行流水归档。
    ctx = service.SyncContext(db_session)
    assert ctx.index.resolve(account_no="622200000001").partner.id == partner.id
    assert ctx.index.resolve(account_no="755900000002").partner.id == partner.id


def test_update_partner_can_change_primary_account_and_former_names(db_session):
    partner = service.create_partner(
        db_session,
        {
            "name": "主账户切换供应商",
            "roles": ["supplier"],
            "bank_accounts": [
                {"bank_name": "银行A", "account_no": "10001", "account_name": "A户", "is_primary": True},
                {"bank_name": "银行B", "account_no": "10002", "account_name": "B户", "is_primary": False},
            ],
            "former_names": ["旧名称A"],
        },
    )
    db_session.flush()

    service.update_partner(
        db_session,
        partner.id,
        {
            "name": "主账户切换供应商",
            "roles": ["supplier"],
            "tax_no": "",
            "contact": "",
            "phone": "",
            "address": "",
            "notes": "",
            "bank_accounts": [
                {"bank_name": "银行A", "account_no": "10001", "account_name": "A户", "is_primary": False},
                {"bank_name": "银行B", "account_no": "10002", "account_name": "B户", "is_primary": True},
            ],
            "former_names": ["旧名称B"],
        },
    )
    db_session.flush()

    detail = service.partner_detail(db_session, partner.id)
    assert detail is not None
    assert detail["bankAccountNo"] == "10002"
    assert detail["bankName"] == "银行B"
    assert detail["bankAccountName"] == "B户"
    assert detail["formerNames"] == ["旧名称B"]
    assert [row["accountNo"] for row in detail["bankAccounts"]] == ["10001", "10002"]
    assert [row["isPrimary"] for row in detail["bankAccounts"]] == [False, True]



def test_partner_archive_counts_standalone_jackyun_purchase_as_real_purchase(db_session):
    jpo = JackyunPurchaseOrder(
        jackyun_purch_id="JKY-PARTNER-HEJIN-001",
        purch_no="CG-HEJIN-001",
        supplier_name="合锦（广州）供应链有限公司",
        amount=Decimal("3080.00"),
        status="completed",
    )
    db_session.add(jpo)
    db_session.flush()

    service.sync_business_partners(db_session)
    items = service.list_partners(db_session, keyword="合锦")["items"]

    assert len(items) == 1
    detail = service.partner_detail(db_session, items[0]["id"])
    assert detail is not None
    assert detail["summary"]["purchaseOrderCount"] == 1
    assert detail["summary"]["purchaseAmount"] == 3080.0
    assert len(detail["purchases"]) == 1
    assert detail["purchases"][0]["sourceType"] == "jackyun_purchase_order"
    assert detail["purchases"][0]["no"] == "CG-HEJIN-001"


def test_partner_archive_does_not_double_count_linked_jackyun_purchase(db_session):
    external = ExternalPurchaseOrder(
        external_order_id="BP-EXT-JKY-001",
        platform="other",
        supplier_name="采购去重供应商",
        order_amount=Decimal("600.00"),
        paid_amount=Decimal("600.00"),
    )
    jpo = JackyunPurchaseOrder(
        jackyun_purch_id="BP-JKY-LINKED-001",
        purch_no="BP-CG-LINKED-001",
        supplier_name="采购去重供应商",
        amount=Decimal("600.00"),
        status="completed",
    )
    db_session.add_all([external, jpo])
    db_session.flush()
    db_session.add(
        JackyunPurchaseOrderLink(
            po_id=external.id,
            jackyun_po_id=jpo.id,
            relation_kind="",
            alloc_amount=Decimal("600.00"),
            note="",
        )
    )
    db_session.flush()

    service.sync_business_partners(db_session)
    item = service.list_partners(db_session, keyword="采购去重供应商")["items"][0]
    detail = service.partner_detail(db_session, item["id"])

    assert detail is not None
    assert detail["summary"]["purchaseOrderCount"] == 1
    assert detail["summary"]["purchaseAmount"] == 600.0
