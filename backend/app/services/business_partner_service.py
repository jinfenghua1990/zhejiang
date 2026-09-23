"""财务中心的统一往来单位档案。

这里的核心约束是：采购单、发票、银行流水、销售单仍是各自的事实源；
``business_partner_links`` 只保存可追溯的归属关系。名称相近没有被税号、账号、
精确别名或人工确认支撑时，绝不自动合并。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import re
import unicodedata
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models.alibaba1688_import import Alibaba1688Order
from app.models.bank import BankTransaction
from app.models.business_partner import (
    BusinessPartner,
    BusinessPartnerBankAccount,
    BusinessPartnerDuplicateReview,
    BusinessPartnerIdentifier,
    BusinessPartnerLink,
    BusinessPartnerRole,
)
from app.models.consumable_purchase import ConsumablePurchase
from app.models.finance import FinanceLegalEntity
from app.models.jackyun import (
    JackyunGoodsDocument,
    JackyunPurchaseReturn,
    JackyunPurchaseSettlement,
)
from app.models.jky_web import JkyWebSalesOrder, JkyWebStockinOrder
from app.models.purchase import ExternalPurchaseOrder, JackyunPurchaseOrder, JackyunPurchaseOrderLink, Supplier
from app.models.tax import TaxInvoice, TaxInvoiceLink


PARTNER_ROLES = {"supplier", "customer", "counterparty"}
IDENTIFIER_KINDS = {
    "name", "alias", "former_name", "tax_no", "bank_account",
    "customer_code", "platform_account",
}
SOURCE_LABELS = {
    "supplier": "历史供应商档案",
    "external_purchase_order": "采购订单",
    "alibaba1688_order": "1688 采购订单",
    "jackyun_purchase_order": "吉客云采购单",
    "jackyun_purchase_settlement": "吉客云采购结算单",
    "jackyun_purchase_return": "吉客云采购退货单",
    "consumable_purchase": "耗材采购单",
    "inbound_document": "采购入库单",
    "jky_web_stockin_order": "吉客云采购入库单",
    "tax_invoice": "税务发票",
    "bank_transaction": "银行流水",
    "jky_web_sales_order": "吉客云销售订单",
}


def normalize_name(value: Any) -> str:
    """仅处理确定的格式差异；不移除公司后缀或括号内主体信息。"""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    return re.sub(r"\s+", "", text)


def normalize_tax_no(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).upper()


def normalize_account(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[\s-]+", "", unicodedata.normalize("NFKC", str(value))).upper()


def normalize_identifier(kind: str, value: Any) -> str:
    if kind == "tax_no":
        return normalize_tax_no(value)
    if kind == "bank_account":
        return normalize_account(value)
    if kind in {"name", "alias", "former_name"}:
        return normalize_name(value)
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or ""))).casefold()


def loose_name_key(value: Any) -> str:
    """只用于给人看待确认候选，永远不作为自动关联依据。"""
    text = normalize_name(value)
    if not text:
        return ""
    text = re.sub(r"\([^)]*\)", "", text)
    for suffix in ("有限责任公司", "股份有限公司", "有限公司", "普通合伙", "个体工商户", "经营部", "商行"):
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", text)


def _decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value))


def _number(value: Decimal | int | float | None) -> float:
    return float(value or 0)


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _roles(value: Iterable[str] | None) -> list[str]:
    return sorted({role for role in (value or []) if role in PARTNER_ROLES})


def _normalized_bank_accounts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """把编辑表单里的多银行账户收敛成稳定结构；账号是唯一键。"""
    raw_accounts = payload.get("bank_accounts")
    if raw_accounts is None:
        # 兼容旧客户端：仍只提交 bank_name / bank_account_no / bank_account_name。
        legacy_no = normalize_account(payload.get("bank_account_no"))
        if not legacy_no:
            return []
        raw_accounts = [{
            "bank_name": payload.get("bank_name") or "",
            "account_no": legacy_no,
            "account_name": payload.get("bank_account_name") or "",
            "is_primary": True,
        }]

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_accounts or []:
        if not isinstance(raw, dict):
            continue
        bank_name = str(raw.get("bank_name") or raw.get("bankName") or "").strip()
        account_no = normalize_account(raw.get("account_no") or raw.get("accountNo"))
        account_name = str(raw.get("account_name") or raw.get("accountName") or "").strip()
        is_primary = bool(raw.get("is_primary") if "is_primary" in raw else raw.get("isPrimary"))
        if not bank_name and not account_no and not account_name:
            continue
        if not account_no:
            raise ValueError("银行账户必须填写银行账号")
        if account_no in seen:
            raise ValueError(f"银行账号重复：{account_no}")
        seen.add(account_no)
        rows.append({
            "bank_name": bank_name,
            "account_no": account_no,
            "account_name": account_name,
            "is_primary": is_primary,
        })

    if rows:
        primary_index = next((index for index, row in enumerate(rows) if row["is_primary"]), 0)
        for index, row in enumerate(rows):
            row["is_primary"] = index == primary_index
    return rows


def _primary_bank_account(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return next((row for row in rows if row.get("is_primary")), rows[0] if rows else {})


def _bank_accounts_payload(db: Session, partner: BusinessPartner) -> list[dict[str, Any]]:
    """Read the relational bank-account master first; JSON is compatibility fallback only."""
    relation_rows = (
        db.query(BusinessPartnerBankAccount)
        .filter(
            BusinessPartnerBankAccount.partner_id == partner.id,
            BusinessPartnerBankAccount.status == "active",
        )
        .order_by(
            BusinessPartnerBankAccount.is_primary.desc(),
            BusinessPartnerBankAccount.id.asc(),
        )
        .all()
    )
    if relation_rows:
        return [
            {
                "bankName": row.bank_name or "",
                "accountNo": row.normalized_account_no or normalize_account(row.account_no),
                "accountName": row.account_name or "",
                "isPrimary": bool(row.is_primary),
            }
            for row in relation_rows
        ]

    raw_rows = partner.bank_accounts if isinstance(partner.bank_accounts, list) else []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        account_no = normalize_account(raw.get("account_no") or raw.get("accountNo"))
        if not account_no or account_no in seen:
            continue
        seen.add(account_no)
        rows.append({
            "bankName": str(raw.get("bank_name") or raw.get("bankName") or "").strip(),
            "accountNo": account_no,
            "accountName": str(raw.get("account_name") or raw.get("accountName") or "").strip(),
            "isPrimary": bool(raw.get("is_primary") if "is_primary" in raw else raw.get("isPrimary")),
        })
    if not rows and normalize_account(partner.bank_account_no):
        rows.append({
            "bankName": partner.bank_name or "",
            "accountNo": normalize_account(partner.bank_account_no),
            "accountName": partner.bank_account_name or "",
            "isPrimary": True,
        })
    if rows and not any(row["isPrimary"] for row in rows):
        rows[0]["isPrimary"] = True
    return rows


def _replace_manual_identifiers(
    db: Session,
    partner: BusinessPartner,
    *,
    kind: str,
    values: list[str],
    primary_value: str = "",
) -> None:
    """编辑主档时替换该类人工维护识别值；来源事实自动生成的 identifier 不删除。"""
    desired: dict[str, str] = {}
    for value in values:
        clean = str(value or "").strip()
        normalized = normalize_identifier(kind, clean)
        if clean and normalized:
            desired.setdefault(normalized, clean)

    existing = (
        db.query(BusinessPartnerIdentifier)
        .filter(
            BusinessPartnerIdentifier.partner_id == partner.id,
            BusinessPartnerIdentifier.kind == kind,
        )
        .all()
    )
    for row in existing:
        normalized = normalize_identifier(kind, row.normalized_value or row.value)
        if row.source == "manual" and normalized not in desired:
            db.delete(row)
        if kind == "bank_account":
            row.is_primary = bool(primary_value and normalized == normalize_identifier(kind, primary_value))
    db.flush()

    ctx = SyncContext(db)
    primary_normalized = normalize_identifier(kind, primary_value)
    for normalized, clean in desired.items():
        _add_identifier(
            ctx,
            partner,
            kind=kind,
            value=clean,
            source="manual",
            is_primary=bool(primary_normalized and normalized == primary_normalized),
        )


def _source_key(source_type: str, source_id: int, relation_role: str) -> tuple[str, int, str]:
    return source_type, int(source_id), relation_role


@dataclass
class Resolution:
    partner: BusinessPartner | None
    method: str = ""
    candidates: list[int] | None = None
    confidence: float | None = None


class PartnerIndex:
    """本次同步内存中的身份索引，避免每条来源记录逐条查询。"""

    def __init__(self, partners: list[BusinessPartner], identifiers: list[BusinessPartnerIdentifier]):
        self.partners: dict[int, BusinessPartner] = {}
        self.by_tax: dict[str, set[int]] = defaultdict(set)
        self.by_account: dict[str, set[int]] = defaultdict(set)
        self.by_name: dict[str, set[int]] = defaultdict(set)
        self.by_customer_code: dict[str, set[int]] = defaultdict(set)
        self.by_platform_account: dict[str, set[int]] = defaultdict(set)
        self.by_canonical_name: dict[str, set[int]] = defaultdict(set)
        self.by_loose_name: dict[str, set[int]] = defaultdict(set)
        self.add_many(partners)
        for row in identifiers:
            self.add_identifier(row.partner_id, row.kind, row.normalized_value)

    def add_many(self, rows: Iterable[BusinessPartner]) -> None:
        for row in rows:
            self.partners[row.id] = row
            self.add_identifier(row.id, "name", row.normalized_name)
            self.add_identifier(row.id, "tax_no", normalize_tax_no(row.tax_no))
            self.add_identifier(row.id, "bank_account", normalize_account(row.bank_account_no))

    def add_identifier(self, partner_id: int, kind: str, normalized_value: str) -> None:
        value = normalized_value or ""
        if not value:
            return
        if kind == "tax_no":
            self.by_tax[value].add(partner_id)
        elif kind == "bank_account":
            self.by_account[value].add(partner_id)
        elif kind == "customer_code":
            self.by_customer_code[value].add(partner_id)
        elif kind == "platform_account":
            self.by_platform_account[value].add(partner_id)
        elif kind in {"name", "alias", "former_name"}:
            self.by_name[value].add(partner_id)
            if kind == "name":
                # 规范名称单独索引：曾用名/别名命中不能覆盖它（见 resolve）。
                self.by_canonical_name[value].add(partner_id)
            loose = loose_name_key(value)
            if loose:
                self.by_loose_name[loose].add(partner_id)

    @staticmethod
    def _active(ids: Iterable[int], partners: dict[int, BusinessPartner]) -> list[int]:
        return sorted({pid for pid in ids if partners.get(pid) is not None and partners[pid].status == "active"})

    def resolve(
        self,
        *,
        name: str = "",
        tax_no: str = "",
        account_no: str = "",
        customer_code: str = "",
        platform_account: str = "",
    ) -> Resolution:
        tax = normalize_tax_no(tax_no)
        account = normalize_account(account_no)
        customer = normalize_identifier("customer_code", customer_code)
        platform_id = normalize_identifier("platform_account", platform_account)
        exact_name = normalize_name(name)

        if tax:
            tax_ids = self._active(self.by_tax.get(tax, set()), self.partners)
            if len(tax_ids) == 1:
                return Resolution(self.partners[tax_ids[0]], "tax_no", confidence=1.0)
            if len(tax_ids) > 1:
                return Resolution(None, candidates=tax_ids)

        if account:
            account_ids = self._active(self.by_account.get(account, set()), self.partners)
            if len(account_ids) == 1:
                return Resolution(self.partners[account_ids[0]], "bank_account", confidence=0.99)
            if len(account_ids) > 1:
                return Resolution(None, candidates=account_ids)

        if customer:
            customer_ids = self._active(self.by_customer_code.get(customer, set()), self.partners)
            if len(customer_ids) == 1:
                return Resolution(self.partners[customer_ids[0]], "customer_code", confidence=1.0)
            if len(customer_ids) > 1:
                return Resolution(None, candidates=customer_ids)

        if platform_id:
            platform_ids = self._active(self.by_platform_account.get(platform_id, set()), self.partners)
            if len(platform_ids) == 1:
                return Resolution(self.partners[platform_ids[0]], "platform_account", confidence=0.995)
            if len(platform_ids) > 1:
                return Resolution(None, candidates=platform_ids)

        if exact_name:
            canonical_ids = self._active(self.by_canonical_name.get(exact_name, set()), self.partners)
            name_ids = self._active(self.by_name.get(exact_name, set()), self.partners)
            # 来源提供税号但同名主档已有不同税号时，不能用同名绕过冲突。
            if tax and any(
                (known_tax := normalize_tax_no(self.partners[pid].tax_no)) and known_tax != tax
                for pid in name_ids
            ):
                return Resolution(None, candidates=name_ids)
            # 规范名称优先：确认“曾用名”后，旧名来源按规范名称唯一归属，不会被判成多命中。
            if len(canonical_ids) == 1:
                return Resolution(self.partners[canonical_ids[0]], "exact_name", confidence=0.95)
            if len(name_ids) == 1:
                return Resolution(self.partners[name_ids[0]], "exact_name", confidence=0.95)
            if len(name_ids) > 1:
                return Resolution(None, candidates=name_ids)

        loose = loose_name_key(name)
        if loose:
            loose_ids = self._active(self.by_loose_name.get(loose, set()), self.partners)
            if loose_ids:
                return Resolution(None, candidates=loose_ids)
        return Resolution(None)


class SyncContext:
    def __init__(self, db: Session):
        self.db = db
        partners = db.query(BusinessPartner).all()
        identifiers = db.query(BusinessPartnerIdentifier).all()
        self.index = PartnerIndex(partners, identifiers)
        self.identifier_by_key = {
            (row.partner_id, row.kind, row.normalized_value): row
            for row in identifiers
        }
        links = db.query(BusinessPartnerLink).all()
        self.link_by_key = {
            _source_key(row.source_type, row.source_id, row.relation_role): row
            for row in links
        }
        self.created_partners = 0
        self.updated_partners = 0
        self.created_links = 0
        self.updated_links = 0
        self.needs_review = 0


def _add_roles(partner: BusinessPartner, values: Iterable[str]) -> bool:
    before = _roles(partner.roles)
    after = _roles([*before, *values])
    if after == before:
        return False
    partner.roles = after
    return True


def _add_identifier(
    ctx: SyncContext,
    partner: BusinessPartner,
    *,
    kind: str,
    value: str,
    source: str,
    is_primary: bool = False,
) -> bool:
    if kind not in IDENTIFIER_KINDS:
        raise ValueError("不支持的往来单位识别字段")
    normalized = normalize_identifier(kind, value)
    if not normalized:
        return False
    key = (partner.id, kind, normalized)
    row = ctx.identifier_by_key.get(key)
    if row is not None:
        if is_primary and not row.is_primary:
            row.is_primary = True
            return True
        return False
    row = BusinessPartnerIdentifier(
        partner_id=partner.id,
        kind=kind,
        value=str(value).strip(),
        normalized_value=normalized,
        is_primary=is_primary,
        source=source,
    )
    ctx.db.add(row)
    ctx.db.flush()
    ctx.identifier_by_key[key] = row
    ctx.index.add_identifier(partner.id, kind, normalized)
    return True


def _apply_identity(
    ctx: SyncContext,
    partner: BusinessPartner,
    *,
    name: str = "",
    tax_no: str = "",
    account_no: str = "",
    account_name: str = "",
    bank_name: str = "",
    roles: Iterable[str] = (),
    source: str,
) -> None:
    changed = _add_roles(partner, roles)
    clean_name = str(name or "").strip()
    clean_tax = normalize_tax_no(tax_no)
    clean_account = normalize_account(account_no)

    if clean_name:
        kind = "name" if normalize_name(clean_name) == partner.normalized_name else "alias"
        changed = _add_identifier(ctx, partner, kind=kind, value=clean_name, source=source) or changed
    if clean_tax:
        changed = _add_identifier(ctx, partner, kind="tax_no", value=clean_tax, source=source) or changed
        if not normalize_tax_no(partner.tax_no):
            partner.tax_no = clean_tax
            changed = True
    if clean_account:
        changed = _add_identifier(ctx, partner, kind="bank_account", value=clean_account, source=source) or changed
        if not normalize_account(partner.bank_account_no):
            partner.bank_account_no = clean_account
            partner.bank_account_name = str(account_name or "").strip()
            partner.bank_name = str(bank_name or "").strip()
            changed = True
    if changed:
        ctx.updated_partners += 1


def _create_partner(
    ctx: SyncContext,
    *,
    name: str,
    tax_no: str = "",
    account_no: str = "",
    account_name: str = "",
    bank_name: str = "",
    roles: Iterable[str] = (),
    source: str,
    legacy_supplier_id: int | None = None,
    contact: str = "",
    phone: str = "",
    address: str = "",
    notes: str = "",
) -> BusinessPartner:
    clean_name = str(name or "").strip() or normalize_tax_no(tax_no) or normalize_account(account_no)
    if not clean_name:
        raise ValueError("往来单位缺少名称、税号和账号，无法建档")
    partner = BusinessPartner(
        legacy_supplier_id=legacy_supplier_id,
        name=clean_name,
        normalized_name=normalize_name(clean_name),
        tax_no=normalize_tax_no(tax_no),
        contact=str(contact or "").strip(),
        phone=str(phone or "").strip(),
        address=str(address or "").strip(),
        bank_name=str(bank_name or "").strip(),
        bank_account_no=normalize_account(account_no),
        bank_account_name=str(account_name or "").strip(),
        roles=_roles(roles),
        notes=str(notes or "").strip(),
    )
    ctx.db.add(partner)
    ctx.db.flush()
    ctx.index.add_many([partner])
    _add_identifier(ctx, partner, kind="name", value=clean_name, source=source, is_primary=True)
    if partner.tax_no:
        _add_identifier(ctx, partner, kind="tax_no", value=partner.tax_no, source=source, is_primary=True)
    if partner.bank_account_no:
        _add_identifier(ctx, partner, kind="bank_account", value=partner.bank_account_no, source=source, is_primary=True)
    ctx.created_partners += 1
    return partner


def _write_link(
    ctx: SyncContext,
    *,
    source_type: str,
    source_id: int,
    relation_role: str,
    raw_name: str = "",
    raw_tax_no: str = "",
    raw_account_no: str = "",
    resolution: Resolution | None = None,
    roles: Iterable[str] = (),
    account_name: str = "",
    bank_name: str = "",
    force_partner: BusinessPartner | None = None,
    force_method: str = "",
    force_evidence: dict[str, Any] | None = None,
) -> BusinessPartnerLink:
    key = _source_key(source_type, source_id, relation_role)
    row = ctx.link_by_key.get(key)
    if row is not None and row.confirmed and row.match_method == "manual" and row.partner_id:
        # 人工确认永远高于后续导入/自动同步；只更新原始来源字段。
        row.raw_name = str(raw_name or "").strip()
        row.raw_tax_no = normalize_tax_no(raw_tax_no)
        row.raw_account_no = normalize_account(raw_account_no)
        ctx.updated_links += 1
        return row

    chosen = force_partner or (resolution.partner if resolution else None)
    method = force_method or (resolution.method if resolution else "")
    candidates = [] if chosen else list((resolution.candidates if resolution else []) or [])
    evidence = dict(force_evidence or {})
    if candidates:
        evidence["candidatePartnerIds"] = candidates

    if chosen is None and not candidates:
        chosen = _create_partner(
            ctx,
            name=raw_name,
            tax_no=raw_tax_no,
            account_no=raw_account_no,
            account_name=account_name,
            bank_name=bank_name,
            roles=roles,
            source=source_type,
            notes=f"由{SOURCE_LABELS.get(source_type, source_type)}自动建档",
        )
        method = "source_created"

    if chosen is not None:
        _apply_identity(
            ctx,
            chosen,
            name=raw_name,
            tax_no=raw_tax_no,
            account_no=raw_account_no,
            account_name=account_name,
            bank_name=bank_name,
            roles=roles,
            source=source_type,
        )
        target = {
            "partner_id": chosen.id,
            "status": "linked",
            "match_method": method,
            "confidence": 1.0 if method in {"tax_no", "source_created", "manual", "invoice_payment_link"} else (resolution.confidence if resolution else 0.95),
            "candidate_partner_ids": [],
            "evidence": evidence,
        }
    else:
        target = {
            "partner_id": None,
            "status": "needs_review",
            "match_method": "",
            "confidence": None,
            "candidate_partner_ids": candidates,
            "evidence": evidence,
        }
        ctx.needs_review += 1

    raw = {
        "raw_name": str(raw_name or "").strip(),
        "raw_tax_no": normalize_tax_no(raw_tax_no),
        "raw_account_no": normalize_account(raw_account_no),
    }
    if row is None:
        row = BusinessPartnerLink(
            source_type=source_type,
            source_id=source_id,
            relation_role=relation_role,
            **raw,
            **target,
        )
        ctx.db.add(row)
        ctx.db.flush()
        ctx.link_by_key[key] = row
        ctx.created_links += 1
    else:
        changed = False
        for field, value in {**raw, **target}.items():
            if getattr(row, field) != value:
                setattr(row, field, value)
                changed = True
        if changed:
            ctx.updated_links += 1
    return row


def _sync_supplier_master(ctx: SyncContext) -> None:
    """Treat Supplier rows as procurement profiles of a canonical BusinessPartner.

    Multiple Supplier rows (for example different platform shops) may point to one partner.
    Only the designated legacy_supplier_id profile may drive the canonical display name;
    other profile names become aliases instead of creating/renaming identities.
    """
    for supplier in ctx.db.query(Supplier).order_by(Supplier.id).all():
        partner = (
            ctx.index.partners.get(int(supplier.partner_id))
            if supplier.partner_id is not None
            else None
        )
        if partner is not None and partner.status == "archived":
            partner = None

        if partner is None:
            partner = next(
                (
                    row for row in ctx.index.partners.values()
                    if row.status == "active" and row.legacy_supplier_id == supplier.id
                ),
                None,
            )

        if partner is None:
            resolution = ctx.index.resolve(
                name=supplier.name,
                tax_no=supplier.tax_no,
                account_no=supplier.bank_account_no,
            )
            if resolution.partner is not None:
                partner = resolution.partner
            else:
                # Ambiguous candidates are not auto-merged. The Supplier profile itself is
                # a real procurement fact, so it gets an independent canonical partner.
                partner = _create_partner(
                    ctx,
                    name=supplier.name,
                    tax_no=supplier.tax_no,
                    account_no=supplier.bank_account_no,
                    account_name=supplier.bank_account_name,
                    bank_name=supplier.bank_name,
                    roles=["supplier"],
                    source="supplier",
                    legacy_supplier_id=supplier.id,
                    contact=supplier.contact,
                    phone=supplier.phone,
                    address=supplier.address,
                    notes=supplier.notes,
                )

        if supplier.partner_id != partner.id:
            supplier.partner_id = partner.id
        if partner.legacy_supplier_id is None:
            partner.legacy_supplier_id = supplier.id
            ctx.updated_partners += 1

        new_name = str(supplier.name or "").strip()
        if new_name and normalize_name(new_name) != partner.normalized_name:
            # V2: Supplier 是渠道/采购画像。即使它曾是 legacy_supplier_id，
            # 名称也只能作为 alias 参与识别，不能反向重命名 canonical 主体。
            _add_identifier(ctx, partner, kind="alias", value=new_name, source="supplier")

        if not partner.contact and supplier.contact:
            partner.contact = supplier.contact
        if not partner.phone and supplier.phone:
            partner.phone = supplier.phone
        if not partner.address and supplier.address:
            partner.address = supplier.address

        _apply_identity(
            ctx,
            partner,
            name=supplier.name,
            tax_no=supplier.tax_no,
            account_no=supplier.bank_account_no,
            account_name=supplier.bank_account_name,
            bank_name=supplier.bank_name,
            roles=["supplier"],
            source="supplier",
        )
        if supplier.external_shop_id:
            _add_identifier(
                ctx,
                partner,
                kind="platform_account",
                value=f"{(supplier.platform or 'supplier').strip().lower()}:{supplier.external_shop_id}",
                source="supplier",
            )
        _write_link(
            ctx,
            source_type="supplier",
            source_id=supplier.id,
            relation_role="supplier",
            raw_name=supplier.name,
            raw_tax_no=supplier.tax_no,
            raw_account_no=supplier.bank_account_no,
            roles=["supplier"],
            force_partner=partner,
            force_method="supplier_profile",
        )

def _sync_procurement_sources(ctx: SyncContext) -> None:
    def source(
        source_type: str,
        rows: Iterable[Any],
        *,
        name_getter,
        partner_id_getter=None,
        platform_account_getter=None,
    ) -> None:
        for row in rows:
            raw_name = str(name_getter(row) or "").strip()
            platform_account = (
                str(platform_account_getter(row) or "").strip()
                if platform_account_getter
                else ""
            )
            partner_id = int(partner_id_getter(row) or 0) if partner_id_getter else 0
            force_partner = ctx.index.partners.get(partner_id) if partner_id else None
            if not raw_name and not platform_account and force_partner is None:
                continue
            _write_link(
                ctx,
                source_type=source_type,
                source_id=row.id,
                relation_role="supplier",
                raw_name=raw_name,
                roles=["supplier"],
                force_partner=force_partner,
                force_method="direct_partner_fk" if force_partner else None,
                resolution=None if force_partner else ctx.index.resolve(
                    name=raw_name,
                    platform_account=platform_account,
                ),
            )

    source(
        "external_purchase_order",
        ctx.db.query(ExternalPurchaseOrder).all(),
        name_getter=lambda row: row.supplier_name,
        partner_id_getter=lambda row: row.supplier_partner_id,
    )
    source(
        "alibaba1688_order",
        ctx.db.query(Alibaba1688Order).filter(Alibaba1688Order.row_status != "deleted").all(),
        name_getter=lambda row: row.seller_company_name or row.seller_member_name,
        partner_id_getter=lambda row: row.supplier_partner_id,
        platform_account_getter=lambda row: (
            f"1688:{row.seller_member_name}" if row.seller_member_name else ""
        ),
    )
    source(
        "jackyun_purchase_order",
        ctx.db.query(JackyunPurchaseOrder).all(),
        name_getter=lambda row: row.supplier_name,
        partner_id_getter=lambda row: row.supplier_partner_id,
    )
    source(
        "jackyun_purchase_settlement",
        ctx.db.query(JackyunPurchaseSettlement).all(),
        name_getter=lambda row: row.supplier_name,
        partner_id_getter=lambda row: row.supplier_partner_id,
    )
    source(
        "jackyun_purchase_return",
        ctx.db.query(JackyunPurchaseReturn).all(),
        name_getter=lambda row: row.supplier_name,
        partner_id_getter=lambda row: row.supplier_partner_id,
    )
    source(
        "consumable_purchase",
        ctx.db.query(ConsumablePurchase).filter(ConsumablePurchase.status != "cancelled").all(),
        name_getter=lambda row: row.supplier_name,
        partner_id_getter=lambda row: row.supplier_partner_id,
    )
    source(
        "inbound_document",
        ctx.db.query(JackyunGoodsDocument).filter(
            JackyunGoodsDocument.document_type == "inbound"
        ).all(),
        name_getter=lambda row: row.supplier_name,
        partner_id_getter=lambda row: row.supplier_partner_id,
    )
    source(
        "jky_web_stockin_order",
        ctx.db.query(JkyWebStockinOrder).all(),
        name_getter=lambda row: row.supplier_name,
        partner_id_getter=lambda row: row.supplier_partner_id,
    )

    # 1688 seller member 是比展示名称更稳定的平台身份；一旦订单已归到主体，
    # 将其沉淀为 platform_account，后续同 member 的不同店铺名仍回到同一主体。
    for row in (
        ctx.db.query(Alibaba1688Order)
        .filter(Alibaba1688Order.row_status != "deleted")
        .all()
    ):
        member = str(row.seller_member_name or "").strip()
        if not member:
            continue
        link = ctx.link_by_key.get(
            _source_key("alibaba1688_order", row.id, "supplier")
        )
        if link is None or link.partner_id is None or link.status != "linked":
            continue
        partner = ctx.index.partners.get(int(link.partner_id))
        if partner is None or partner.status != "active":
            continue
        _add_identifier(
            ctx,
            partner,
            kind="platform_account",
            value=f"1688:{member}",
            source="alibaba1688_order",
        )


def _own_entity_keys(db: Session) -> tuple[set[str], set[str]]:
    names = {normalize_name(row.name) for row in db.query(FinanceLegalEntity).all() if row.name}
    taxes = {normalize_tax_no(row.tax_id) for row in db.query(FinanceLegalEntity).all() if row.tax_id}
    # finance_legal_entities 尚未维护税号的历史环境，也要识别默认主体，避免把自己建成往来单位。
    names.add(normalize_name("浙江柴本网络科技有限公司"))
    return names, taxes


def _is_own_entity(name: str, tax_no: str, own_names: set[str], own_taxes: set[str]) -> bool:
    return bool(
        (normalize_tax_no(tax_no) and normalize_tax_no(tax_no) in own_taxes)
        or (normalize_name(name) and normalize_name(name) in own_names)
    )


def _sync_invoice_sources(ctx: SyncContext) -> None:
    own_names, own_taxes = _own_entity_keys(ctx.db)
    rows = ctx.db.query(TaxInvoice).all()
    for invoice in rows:
        seller_own = _is_own_entity(invoice.seller_name, invoice.seller_tax_id, own_names, own_taxes)
        buyer_own = _is_own_entity(invoice.buyer_name, invoice.buyer_tax_id, own_names, own_taxes)
        direction = (invoice.direction or "unknown").lower()

        if not seller_own and (invoice.seller_name or invoice.seller_tax_id):
            role = "supplier" if buyer_own or direction == "input" else "counterparty"
            force_partner = ctx.index.partners.get(int(invoice.seller_partner_id or 0))
            _write_link(
                ctx,
                source_type="tax_invoice",
                source_id=invoice.id,
                relation_role="seller",
                raw_name=invoice.seller_name,
                raw_tax_no=invoice.seller_tax_id,
                roles=[role],
                force_partner=force_partner,
                force_method="direct_partner_fk" if force_partner else None,
                resolution=None if force_partner else ctx.index.resolve(
                    name=invoice.seller_name,
                    tax_no=invoice.seller_tax_id,
                ),
            )

        if not buyer_own and (invoice.buyer_name or invoice.buyer_tax_id):
            role = "customer" if seller_own or direction == "output" else "counterparty"
            force_partner = ctx.index.partners.get(int(invoice.buyer_partner_id or 0))
            _write_link(
                ctx,
                source_type="tax_invoice",
                source_id=invoice.id,
                relation_role="buyer",
                raw_name=invoice.buyer_name,
                raw_tax_no=invoice.buyer_tax_id,
                roles=[role],
                force_partner=force_partner,
                force_method="direct_partner_fk" if force_partner else None,
                resolution=None if force_partner else ctx.index.resolve(
                    name=invoice.buyer_name,
                    tax_no=invoice.buyer_tax_id,
                ),
            )


def _active_invoice_partner_ids(ctx: SyncContext, txn_id: int) -> list[int]:
    invoice_links = (
        ctx.db.query(TaxInvoiceLink)
        .filter(
            TaxInvoiceLink.target_type == "bank_transaction",
            TaxInvoiceLink.target_id == txn_id,
            TaxInvoiceLink.confirmed.is_(True),
            TaxInvoiceLink.match_method != "rejected",
        )
        .all()
    )
    invoice_ids = {int(row.invoice_id) for row in invoice_links}
    if not invoice_ids:
        return []

    # V2 first uses the direct seller_partner_id on the invoice.
    direct_ids = {
        int(partner_id)
        for (partner_id,) in (
            ctx.db.query(TaxInvoice.seller_partner_id)
            .filter(
                TaxInvoice.id.in_(invoice_ids),
                TaxInvoice.seller_partner_id.isnot(None),
            )
            .all()
        )
        if partner_id is not None
    }
    if direct_ids:
        return sorted(direct_ids)

    # Compatibility fallback for pre-materialization data.
    partner_ids = {
        row.partner_id
        for row in ctx.link_by_key.values()
        if row.source_type == "tax_invoice"
        and row.source_id in invoice_ids
        and row.relation_role == "seller"
        and row.status == "linked"
        and row.partner_id is not None
    }
    return sorted(int(value) for value in partner_ids)


def _sync_bank_sources(ctx: SyncContext) -> None:
    for txn in ctx.db.query(BankTransaction).all():
        direct_partner = ctx.index.partners.get(int(txn.counterparty_partner_id or 0))
        invoice_partner_ids = _active_invoice_partner_ids(ctx, txn.id)
        evidence: dict[str, Any] = {"direction": txn.direction or ""}
        if direct_partner is not None:
            evidence["directPartnerId"] = direct_partner.id
        if invoice_partner_ids:
            evidence["invoicePartnerIds"] = invoice_partner_ids
        raw_name = str(txn.counterparty_name or "").strip()
        raw_account_no = str(txn.counterparty_account or "").strip()
        # 利息、手续费等没有对方户名和账号的流水不属于任何往来单位，既不建档也不进待确认。
        if not raw_name and not raw_account_no and not invoice_partner_ids and direct_partner is None:
            continue
        if direct_partner is not None:
            _write_link(
                ctx,
                source_type="bank_transaction",
                source_id=txn.id,
                relation_role="counterparty",
                raw_name=raw_name,
                raw_account_no=raw_account_no,
                roles=["counterparty"],
                force_partner=direct_partner,
                force_method="direct_partner_fk",
                force_evidence=evidence,
            )
            continue
        if len(invoice_partner_ids) == 1:
            _write_link(
                ctx,
                source_type="bank_transaction",
                source_id=txn.id,
                relation_role="counterparty",
                raw_name=raw_name,
                raw_account_no=raw_account_no,
                roles=["counterparty"],
                force_partner=ctx.index.partners[invoice_partner_ids[0]],
                force_method="invoice_payment_link",
                force_evidence=evidence,
            )
            continue
        resolution = ctx.index.resolve(name=raw_name, account_no=raw_account_no)
        if len(invoice_partner_ids) > 1:
            resolution = Resolution(None, candidates=invoice_partner_ids)
        _write_link(
            ctx,
            source_type="bank_transaction",
            source_id=txn.id,
            relation_role="counterparty",
            raw_name=raw_name,
            raw_account_no=raw_account_no,
            roles=["counterparty"],
            resolution=resolution,
            force_evidence=evidence,
        )


def _customer_name(row: JkyWebSalesOrder) -> str:
    raw = row.raw if isinstance(row.raw, dict) else {}
    for key in ("customerName", "customer_name", "buyerName", "buyer_name", "customerAccount"):
        value = raw.get(key)
        if value:
            return str(value).strip()
    return str(row.customer_account or "").strip()


def _sync_sales_sources(ctx: SyncContext) -> None:
    for row in ctx.db.query(JkyWebSalesOrder).all():
        customer_name = _customer_name(row)
        if not customer_name and not row.customer_code:
            continue
        force_partner = ctx.index.partners.get(int(row.customer_partner_id or 0))
        link = _write_link(
            ctx,
            source_type="jky_web_sales_order",
            source_id=row.id,
            relation_role="customer",
            raw_name=customer_name,
            roles=["customer"],
            force_partner=force_partner,
            force_method="direct_partner_fk" if force_partner else None,
            resolution=None if force_partner else ctx.index.resolve(
                name=customer_name,
                customer_code=row.customer_code or "",
            ),
        )
        if link.partner_id and row.customer_code:
            partner = ctx.index.partners[link.partner_id]
            _add_identifier(
                ctx,
                partner,
                kind="customer_code",
                value=row.customer_code,
                source="jky_web_sales_order",
            )


def sync_business_partners(db: Session) -> dict[str, Any]:
    """从所有已落库事实增量回填统一主体，并物化到业务表 partner 外键。

    BusinessPartnerLink 继续保留审计证据；V2 正常查询路径使用各业务表自己的
    partner_id/supplier_partner_id/customer_partner_id。
    """
    ctx = SyncContext(db)
    _sync_supplier_master(ctx)
    _sync_procurement_sources(ctx)
    _sync_invoice_sources(ctx)
    _sync_bank_sources(ctx)
    _sync_sales_sources(ctx)

    # 局部导入避免 partner_reference_service 反向导入本模块形成循环。
    from app.services.partner_reference_service import materialize_partner_references

    materialized = materialize_partner_references(db)
    return {
        "createdPartners": ctx.created_partners,
        "updatedPartners": ctx.updated_partners,
        "createdLinks": ctx.created_links,
        "updatedLinks": ctx.updated_links,
        "needsReview": ctx.needs_review,
        "materializedRefs": int(materialized.get("materialized", 0)),
        "roleRowsCreated": int(materialized.get("rolesCreated", 0)),
        "bankAccountsChanged": int(materialized.get("bankAccountsChanged", 0)),
    }


def _identifier_dict(row: BusinessPartnerIdentifier) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "value": row.value,
        "isPrimary": bool(row.is_primary),
        "source": row.source,
    }


def _partner_core(db: Session, partner: BusinessPartner) -> dict[str, Any]:
    identifiers = (
        db.query(BusinessPartnerIdentifier)
        .filter(BusinessPartnerIdentifier.partner_id == partner.id)
        .order_by(BusinessPartnerIdentifier.kind, BusinessPartnerIdentifier.id)
        .all()
    )
    return {
        "id": partner.id,
        "name": partner.name,
        "taxNo": partner.tax_no or "",
        "contact": partner.contact or "",
        "phone": partner.phone or "",
        "address": partner.address or "",
        "bankName": partner.bank_name or "",
        "bankAccountNo": partner.bank_account_no or "",
        "bankAccountName": partner.bank_account_name or "",
        "bankAccounts": _bank_accounts_payload(db, partner),
        "roles": _roles(partner.roles),
        "status": partner.status,
        "notes": partner.notes or "",
        "legacySupplierId": partner.legacy_supplier_id,
        # 人工确认“同一主体”后登记的历史名称，来源匹配时和别名同样有效。
        "formerNames": [
            row.value for row in identifiers if row.kind == "former_name"
        ],
        "identifiers": [_identifier_dict(row) for row in identifiers],
        "createdAt": _iso(partner.created_at),
        "updatedAt": _iso(partner.updated_at),
    }


def _source_rows(db: Session, source_type: str, ids: set[int]) -> dict[int, Any]:
    if not ids:
        return {}
    mapping = {
        "external_purchase_order": ExternalPurchaseOrder,
        "alibaba1688_order": Alibaba1688Order,
        "jackyun_purchase_order": JackyunPurchaseOrder,
        "jackyun_purchase_settlement": JackyunPurchaseSettlement,
        "jackyun_purchase_return": JackyunPurchaseReturn,
        "consumable_purchase": ConsumablePurchase,
        "inbound_document": JackyunGoodsDocument,
        "jky_web_stockin_order": JkyWebStockinOrder,
        "tax_invoice": TaxInvoice,
        "bank_transaction": BankTransaction,
        "jky_web_sales_order": JkyWebSalesOrder,
    }
    model = mapping.get(source_type)
    if model is None:
        return {}
    return {row.id: row for row in db.query(model).filter(model.id.in_(ids)).all()}


def _pick_preloaded(
    preload: dict[str, dict[int, Any]] | None,
    db: Session,
    source_type: str,
    ids: set[int],
) -> dict[int, Any]:
    """列表页批量预加载时复用同一张源表，详情页保持逐次查询。"""
    cached = (preload or {}).get(source_type)
    if cached is None:
        return _source_rows(db, source_type, ids)
    return {sid: cached[sid] for sid in ids if sid in cached}


def _purchase_rows(
    db: Session,
    links: list[BusinessPartnerLink],
    *,
    preload: dict[str, dict[int, Any]] | None = None,
    linked_jackyun_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    external_ids = {row.source_id for row in links if row.source_type == "external_purchase_order"}
    alibaba_ids = {row.source_id for row in links if row.source_type == "alibaba1688_order"}
    jackyun_ids = {row.source_id for row in links if row.source_type == "jackyun_purchase_order"}
    consumable_ids = {row.source_id for row in links if row.source_type == "consumable_purchase"}
    external = _pick_preloaded(preload, db, "external_purchase_order", external_ids)
    alibaba = _pick_preloaded(preload, db, "alibaba1688_order", alibaba_ids)
    jackyun = _pick_preloaded(preload, db, "jackyun_purchase_order", jackyun_ids)
    consumable = _pick_preloaded(preload, db, "consumable_purchase", consumable_ids)
    rows: list[dict[str, Any]] = []
    external_1688_nos = {
        str(row.external_order_id or "").strip()
        for row in external.values()
        if (row.platform or "") == "1688"
    }
    for row in external.values():
        paid = row.paid_amount if row.paid_amount is not None else row.order_amount
        rows.append({
            "sourceType": "external_purchase_order",
            "id": row.id,
            "no": row.external_order_id,
            "platform": row.platform or "",
            "title": row.title or "",
            "date": _iso(row.ordered_at),
            "amount": _number(row.order_amount),
            "paidAmount": _number(paid),
            "status": row.purchase_status or row.order_status or "",
        })
    for row in alibaba.values():
        # 同一 1688 订单的工作流副本已经在上面展示，不重复计入金额。
        if str(row.external_order_id or "").strip() in external_1688_nos:
            continue
        rows.append({
            "sourceType": "alibaba1688_order",
            "id": row.id,
            "no": row.external_order_id,
            "platform": "1688",
            "title": "",
            "date": _iso(row.order_time),
            "amount": _number(row.goods_total + row.freight - row.discount),
            "paidAmount": _number(row.actual_payment),
            "status": row.order_status or "",
        })
    linked_jackyun_ids_resolved = (
        linked_jackyun_ids
        if linked_jackyun_ids is not None
        else {int(link.jackyun_po_id) for link in db.query(JackyunPurchaseOrderLink).all()}
    )
    for row in jackyun.values():
        status = str(row.status or "").strip().lower()
        if row.id in linked_jackyun_ids_resolved or any(token in status for token in ("cancel", "取消", "作废", "void")):
            continue
        rows.append({
            "sourceType": "jackyun_purchase_order",
            "id": row.id,
            "no": row.purch_no or row.jackyun_purch_id,
            "platform": "吉客云",
            "title": "",
            "date": _iso(row.created_at),
            "amount": _number(row.amount),
            "paidAmount": None,
            "status": row.status or "",
        })

    for row in consumable.values():
        rows.append({
            "sourceType": "consumable_purchase",
            "id": row.id,
            "no": row.number,
            "platform": "耗材采购",
            "title": row.note or "",
            "date": _iso(row.ordered_on),
            "amount": 0.0,
            "paidAmount": 0.0,
            "status": row.status or "",
        })
    return sorted(rows, key=lambda row: row.get("date") or "", reverse=True)


def _inbound_rows(
    db: Session,
    links: list[BusinessPartnerLink],
    *,
    preload: dict[str, dict[int, Any]] | None = None,
) -> list[dict[str, Any]]:
    doc_ids = {row.source_id for row in links if row.source_type == "inbound_document"}
    web_ids = {row.source_id for row in links if row.source_type == "jky_web_stockin_order"}
    docs = _pick_preloaded(preload, db, "inbound_document", doc_ids)
    web = _pick_preloaded(preload, db, "jky_web_stockin_order", web_ids)
    rows: list[dict[str, Any]] = []
    shown_nos = set()
    for row in docs.values():
        shown_nos.add(row.goodsdoc_no)
        rows.append({
            "sourceType": "inbound_document",
            "id": row.id,
            "no": row.goodsdoc_no,
            "date": _iso(row.document_at),
            "warehouse": row.warehouse_name or "",
            "quantity": _number(row.total_quantity),
            "amount": _number(row.total_amount),
        })
    for row in web.values():
        if row.goodsdoc_no and row.goodsdoc_no in shown_nos:
            continue
        rows.append({
            "sourceType": "jky_web_stockin_order",
            "id": row.id,
            "no": row.goodsdoc_no or row.doc_id,
            "date": _iso(row.in_out_date),
            "warehouse": row.warehouse_name or "",
            "quantity": _number(row.total_quantity),
            "amount": _number(row.has_tax_total_amount if row.has_tax_total_amount is not None else row.cost_total_amount),
        })
    return sorted(rows, key=lambda row: row.get("date") or "", reverse=True)


def _invoice_payment_amounts(db: Session, invoice_ids: set[int]) -> dict[int, Decimal]:
    if not invoice_ids:
        return {}
    invoices = _source_rows(db, "tax_invoice", invoice_ids)
    amounts: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    rows = (
        db.query(TaxInvoiceLink)
        .filter(
            TaxInvoiceLink.invoice_id.in_(invoice_ids),
            TaxInvoiceLink.target_type == "bank_transaction",
            TaxInvoiceLink.confirmed.is_(True),
            TaxInvoiceLink.match_method != "rejected",
        )
        .all()
    )
    for row in rows:
        fallback = invoices.get(row.invoice_id).total_amount if invoices.get(row.invoice_id) else 0
        amounts[row.invoice_id] += _decimal(row.allocated_amount if row.allocated_amount is not None else fallback)
    return amounts


def _invoice_rows(db: Session, links: list[BusinessPartnerLink]) -> list[dict[str, Any]]:
    invoice_ids = {row.source_id for row in links if row.source_type == "tax_invoice"}
    invoices = _source_rows(db, "tax_invoice", invoice_ids)
    paid = _invoice_payment_amounts(db, invoice_ids)
    # 与发票管理页共用同一套跨账期红蓝票派生逻辑，避免往来单位页把已红冲
    # 的蓝票继续当成正常有效金额，或把红字票只显示成一条普通负数发票。
    from app.services.tax_invoice_service import red_accounting_context

    red_context = red_accounting_context(db, list(invoices.values()))
    rows = []
    for row in invoices.values():
        amount = _decimal(row.total_amount)
        bank_paid = paid.get(row.id, Decimal("0"))
        trace = red_context.get(row.id, {})
        effective_amount = _decimal(trace.get("remainingAfterRedAmount"))
        rows.append({
            "id": row.id,
            "no": f"{row.invoice_code or ''}{row.invoice_number or ''}",
            "date": _iso(row.issue_date),
            "direction": row.direction or "unknown",
            "status": row.status or "",
            "sellerName": row.seller_name or "",
            "buyerName": row.buyer_name or "",
            "amount": _number(amount),
            "effectiveAmount": _number(effective_amount),
            "bankPaidAmount": _number(bank_paid),
            "bankRemainingAmount": _number(effective_amount - bank_paid),
            "matchStatus": row.match_status or "",
            "category": row.category or "",
            "verified": bool(row.verified),
            "invoiceColor": trace.get("invoiceColor", "unknown"),
            "invoiceStatusLabel": trace.get("invoiceStatusLabel", "待确认发票"),
            "redStatus": trace.get("redStatus", "unknown"),
            "redOffsetAmount": _number(trace.get("redOffsetAmount")),
            "redRelatedInvoiceNo": trace.get("redRelatedInvoiceNo", ""),
            "accountingException": trace.get("accountingException", ""),
        })
    return sorted(rows, key=lambda row: row.get("date") or "", reverse=True)


def _bank_invoice_refs(db: Session, txn_ids: set[int]) -> dict[int, list[dict[str, Any]]]:
    if not txn_ids:
        return {}
    rows = (
        db.query(TaxInvoiceLink)
        .filter(
            TaxInvoiceLink.target_type == "bank_transaction",
            TaxInvoiceLink.target_id.in_(txn_ids),
            TaxInvoiceLink.confirmed.is_(True),
            TaxInvoiceLink.match_method != "rejected",
        )
        .all()
    )
    invoice_ids = {row.invoice_id for row in rows}
    invoices = _source_rows(db, "tax_invoice", invoice_ids)
    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        invoice = invoices.get(row.invoice_id)
        if invoice is None:
            continue
        result[row.target_id].append({
            "invoiceId": invoice.id,
            "invoiceNo": f"{invoice.invoice_code or ''}{invoice.invoice_number or ''}",
            "allocatedAmount": _number(row.allocated_amount if row.allocated_amount is not None else invoice.total_amount),
        })
    return result


def _bank_rows(db: Session, links: list[BusinessPartnerLink]) -> list[dict[str, Any]]:
    txn_ids = {row.source_id for row in links if row.source_type == "bank_transaction"}
    txns = _source_rows(db, "bank_transaction", txn_ids)
    invoices = _bank_invoice_refs(db, txn_ids)
    rows = []
    for row in txns.values():
        rows.append({
            "id": row.id,
            "date": _iso(row.txn_date),
            "transactionTime": _iso(row.transaction_time),
            "direction": row.direction or "",
            "amount": _number(row.amount),
            "counterpartyName": row.counterparty_name or "",
            "counterpartyAccount": row.counterparty_account or "",
            "summary": row.summary or "",
            "serialNo": row.serial_no or "",
            "voucherNo": row.voucher_no or "",
            "sourceRowNumber": row.source_row_number,
            "rawAvailable": bool(row.raw),
            "rawUrl": f"/api/v1/reconciliation/transactions/{row.id}/raw",
            "invoices": invoices.get(row.id, []),
        })
    return sorted(rows, key=lambda row: (row.get("transactionTime") or row.get("date") or ""), reverse=True)


def _sales_rows(db: Session, links: list[BusinessPartnerLink]) -> list[dict[str, Any]]:
    ids = {row.source_id for row in links if row.source_type == "jky_web_sales_order"}
    source = _source_rows(db, "jky_web_sales_order", ids)
    rows = []
    for row in source.values():
        rows.append({
            "id": row.id,
            "no": row.trade_no,
            "sourceNo": row.source_trade_no or "",
            "date": _iso(row.trade_time),
            "paidAt": _iso(row.pay_time),
            "platform": row.shop_name or "",
            "customerCode": row.customer_code or "",
            "amount": _number(row.payment),
            "paidAmount": _number(row.real_fee),
            "status": row.trade_status or "",
        })
    return sorted(rows, key=lambda row: row.get("date") or "", reverse=True)


def _review_rows(db: Session, partner_id: int) -> list[dict[str, Any]]:
    rows = db.query(BusinessPartnerLink).filter(BusinessPartnerLink.status == "needs_review").all()
    matched = [row for row in rows if partner_id in {int(value) for value in (row.candidate_partner_ids or [])}]
    result: list[dict[str, Any]] = []
    for row in matched:
        preview = _fact_preview(db, row)
        result.append({
            "linkId": row.id,
            "sourceType": row.source_type,
            "sourceLabel": SOURCE_LABELS.get(row.source_type, row.source_type),
            "sourceId": row.source_id,
            "relationRole": row.relation_role,
            "rawName": row.raw_name or "",
            "rawTaxNo": row.raw_tax_no or "",
            "rawAccountNo": row.raw_account_no or "",
            "candidatePartnerIds": row.candidate_partner_ids or [],
            "evidence": row.evidence or {},
            **preview,
        })
    return sorted(result, key=lambda row: row.get("date") or "", reverse=True)


def _fact_preview(db: Session, link: BusinessPartnerLink) -> dict[str, Any]:
    source = _source_rows(db, link.source_type, {link.source_id}).get(link.source_id)
    if source is None:
        return {"no": "", "date": None, "amount": 0.0}
    if isinstance(source, TaxInvoice):
        return {
            "no": f"{source.invoice_code or ''}{source.invoice_number or ''}",
            "date": _iso(source.issue_date),
            "amount": _number(source.total_amount),
        }
    if isinstance(source, BankTransaction):
        return {"no": source.serial_no or source.voucher_no or "", "date": _iso(source.txn_date), "amount": _number(source.amount)}
    if isinstance(source, ExternalPurchaseOrder):
        return {"no": source.external_order_id, "date": _iso(source.ordered_at), "amount": _number(source.paid_amount if source.paid_amount is not None else source.order_amount)}
    if isinstance(source, Alibaba1688Order):
        return {"no": source.external_order_id, "date": _iso(source.order_time), "amount": _number(source.actual_payment)}
    if isinstance(source, JackyunGoodsDocument):
        return {"no": source.goodsdoc_no, "date": _iso(source.document_at), "amount": _number(source.total_amount)}
    if isinstance(source, JkyWebStockinOrder):
        return {"no": source.goodsdoc_no or source.doc_id, "date": _iso(source.in_out_date), "amount": _number(source.has_tax_total_amount if source.has_tax_total_amount is not None else source.cost_total_amount)}
    if isinstance(source, JkyWebSalesOrder):
        return {"no": source.trade_no, "date": _iso(source.trade_time), "amount": _number(source.real_fee if source.real_fee is not None else source.payment)}
    return {"no": str(getattr(source, "number", "") or getattr(source, "purch_no", "") or getattr(source, "settlement_no", "") or getattr(source, "return_no", "")), "date": _iso(getattr(source, "ordered_on", None) or getattr(source, "settlement_date", None) or getattr(source, "returned_at_src", None)), "amount": _number(getattr(source, "amount", None) or getattr(source, "settlement_amount", None) or getattr(source, "return_amount", None))}


def _duplicate_map(db: Session) -> dict[str, list[tuple[int, str, str]]]:
    """按松名称键归组，只用于提示“疑似同一主体”，不作为自动合并依据。"""
    groups: dict[str, list[tuple[int, str, str]]] = {}
    rows = (
        db.query(BusinessPartner.id, BusinessPartner.name, BusinessPartner.tax_no)
        .filter(BusinessPartner.status != "archived")
        .all()
    )
    for row in rows:
        key = loose_name_key(row.name)
        if len(key) < 4:
            continue
        groups.setdefault(key, []).append((row.id, row.name or "", row.tax_no or ""))
    return {key: members for key, members in groups.items() if len(members) > 1}


def _duplicate_decisions(db: Session) -> set[tuple[int, int]]:
    """已人工判断过的“疑似同一主体”组合；双向各存一行。"""
    rows = db.query(
        BusinessPartnerDuplicateReview.partner_id,
        BusinessPartnerDuplicateReview.other_partner_id,
    ).all()
    return {(int(row.partner_id), int(row.other_partner_id)) for row in rows}


def _possible_duplicates(
    partner: BusinessPartner,
    duplicate_map: dict[str, list[tuple[int, str, str]]],
    decisions: set[tuple[int, int]] | None = None,
) -> list[dict[str, Any]]:
    key = loose_name_key(partner.name)
    if len(key) < 4:
        return []
    decided = decisions or set()
    return [
        {"id": member_id, "name": name, "taxNo": tax_no}
        for member_id, name, tax_no in duplicate_map.get(key, [])
        if member_id != partner.id and (partner.id, member_id) not in decided
    ]


def partner_detail(
    db: Session,
    partner_id: int,
    *,
    duplicate_map: dict[str, list[tuple[int, str, str]]] | None = None,
    duplicate_decisions: set[tuple[int, int]] | None = None,
) -> dict[str, Any] | None:
    partner = db.get(BusinessPartner, partner_id)
    if partner is None or partner.status == "archived":
        return None
    links = (
        db.query(BusinessPartnerLink)
        .filter(
            BusinessPartnerLink.partner_id == partner.id,
            BusinessPartnerLink.status == "linked",
        )
        .all()
    )
    purchases = _purchase_rows(db, links)
    inbounds = _inbound_rows(db, links)
    invoices = _invoice_rows(db, links)
    payments = _bank_rows(db, links)
    sales = _sales_rows(db, links)
    purchase_amount = sum(
        (
            _decimal(row["paidAmount"])
            if row.get("paidAmount") is not None
            else _decimal(row.get("amount"))
            for row in purchases
        ),
        Decimal("0"),
    )
    inbound_amount = sum((_decimal(row["amount"]) for row in inbounds), Decimal("0"))
    invoice_amount = sum((_decimal(row["amount"]) for row in invoices), Decimal("0"))
    bank_paid = sum((_decimal(row["amount"]) for row in payments if row["direction"] == "out"), Decimal("0"))
    bank_received = sum((_decimal(row["amount"]) for row in payments if row["direction"] == "in"), Decimal("0"))
    sales_amount = sum((_decimal(row["paidAmount"]) for row in sales), Decimal("0"))
    duplicates = _possible_duplicates(
        partner,
        duplicate_map if duplicate_map is not None else _duplicate_map(db),
        duplicate_decisions if duplicate_decisions is not None else _duplicate_decisions(db),
    )
    review_rows = _review_rows(db, partner.id)
    return {
        **_partner_core(db, partner),
        "summary": {
            "purchaseOrderCount": len(purchases),
            "purchaseAmount": _number(purchase_amount),
            "inboundCount": len(inbounds),
            "inboundAmount": _number(inbound_amount),
            "invoiceCount": len(invoices),
            "invoiceAmount": _number(invoice_amount),
            "bankTransactionCount": len(payments),
            "bankPaidAmount": _number(bank_paid),
            "bankReceivedAmount": _number(bank_received),
            # 这是事实金额之间的核对差额，不替代会计应付/应收科目余额。
            "purchasePaymentDifference": _number(purchase_amount - bank_paid),
            "invoicePaymentDifference": _number(invoice_amount - bank_paid),
            "salesOrderCount": len(sales),
            "salesReceivedAmount": _number(sales_amount),
            "needsReviewCount": len(review_rows),
        },
        "purchases": purchases,
        "inbounds": inbounds,
        "invoices": invoices,
        "payments": payments,
        "sales": sales,
        "reviewItems": review_rows,
        # 只提示不合并：疑似同一主体的其他档案，等人工决定
        "possibleDuplicates": duplicates,
    }


def list_partners(
    db: Session,
    *,
    keyword: str = "",
    role: str = "all",
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    rows = (
        db.query(BusinessPartner)
        .filter(BusinessPartner.status != "archived")
        .order_by(BusinessPartner.name.asc(), BusinessPartner.id.asc())
        .all()
    )
    needle = normalize_name(keyword)
    duplicate_map = _duplicate_map(db)
    duplicate_decisions = _duplicate_decisions(db)

    # 批量预加载：列表页只做汇总。此前逐档案调用 partner_detail，
    # 290 个档案要跑 ~3500 条 SQL，接口耗时 8 秒以上。
    identifiers_by_partner: dict[int, list[BusinessPartnerIdentifier]] = defaultdict(list)
    for identifier_row in (
        db.query(BusinessPartnerIdentifier)
        .order_by(BusinessPartnerIdentifier.kind, BusinessPartnerIdentifier.id)
        .all()
    ):
        identifiers_by_partner[identifier_row.partner_id].append(identifier_row)

    links_by_partner: dict[int, list[BusinessPartnerLink]] = defaultdict(list)
    source_ids_by_type: dict[str, set[int]] = defaultdict(set)
    for link in db.query(BusinessPartnerLink).filter(BusinessPartnerLink.status == "linked").all():
        links_by_partner[link.partner_id].append(link)
        source_ids_by_type[link.source_type].add(link.source_id)
    sources = {stype: _source_rows(db, stype, ids) for stype, ids in source_ids_by_type.items()}
    linked_jackyun_ids = (
        {int(link.jackyun_po_id) for link in db.query(JackyunPurchaseOrderLink).all()}
        if "jackyun_purchase_order" in sources
        else set()
    )

    # 待确认计数与 _review_rows 同口径：按 candidate_partner_ids 归属。
    review_counts: dict[int, int] = defaultdict(int)
    for review_link in db.query(BusinessPartnerLink).filter(BusinessPartnerLink.status == "needs_review").all():
        for partner_id in {int(value) for value in (review_link.candidate_partner_ids or [])}:
            review_counts[partner_id] += 1

    items = []
    for row in rows:
        if role in PARTNER_ROLES and role not in set(row.roles or []):
            continue
        identifiers = identifiers_by_partner.get(row.id, [])
        if needle:
            searchable = " ".join(
                [
                    row.name or "",
                    row.tax_no or "",
                    row.bank_account_no or "",
                    *[str(item.value) for item in identifiers],
                ]
            )
            if needle not in normalize_name(searchable) and needle not in normalize_tax_no(searchable):
                continue
        links = links_by_partner.get(row.id, [])
        purchases = _purchase_rows(db, links, preload=sources, linked_jackyun_ids=linked_jackyun_ids)
        inbounds = _inbound_rows(db, links, preload=sources)
        invoice_rows = _pick_preloaded(
            sources, db, "tax_invoice",
            {link.source_id for link in links if link.source_type == "tax_invoice"},
        )
        bank_rows = _pick_preloaded(
            sources, db, "bank_transaction",
            {link.source_id for link in links if link.source_type == "bank_transaction"},
        )
        sales_rows = _pick_preloaded(
            sources, db, "jky_web_sales_order",
            {link.source_id for link in links if link.source_type == "jky_web_sales_order"},
        )
        purchase_amount = sum(
            (
                _decimal(purchase["paidAmount"])
                if purchase.get("paidAmount") is not None
                else _decimal(purchase.get("amount"))
                for purchase in purchases
            ),
            Decimal("0"),
        )
        invoice_amount = sum((_decimal(inv.total_amount) for inv in invoice_rows.values()), Decimal("0"))
        bank_paid = sum(
            (_decimal(txn.amount) for txn in bank_rows.values() if (txn.direction or "") == "out"),
            Decimal("0"),
        )
        bank_received = sum(
            (_decimal(txn.amount) for txn in bank_rows.values() if (txn.direction or "") == "in"),
            Decimal("0"),
        )
        sales_amount = sum((_decimal(order.real_fee) for order in sales_rows.values()), Decimal("0"))
        items.append({
            "id": row.id,
            "name": row.name,
            "taxNo": row.tax_no or "",
            "roles": _roles(row.roles),
            "status": row.status,
            "identifiers": [_identifier_dict(item) for item in identifiers],
            "formerNames": [item.value for item in identifiers if item.kind == "former_name"],
            "legacySupplierId": row.legacy_supplier_id,
            "summary": {
                "purchaseOrderCount": len(purchases),
                "purchaseAmount": _number(purchase_amount),
                "inboundCount": len(inbounds),
                "inboundAmount": _number(sum((_decimal(inbound["amount"]) for inbound in inbounds), Decimal("0"))),
                "invoiceCount": len(invoice_rows),
                "invoiceAmount": _number(invoice_amount),
                "bankTransactionCount": len(bank_rows),
                "bankPaidAmount": _number(bank_paid),
                "bankReceivedAmount": _number(bank_received),
                # 这是事实金额之间的核对差额，不替代会计应付/应收科目余额。
                "purchasePaymentDifference": _number(purchase_amount - bank_paid),
                "invoicePaymentDifference": _number(invoice_amount - bank_paid),
                "salesOrderCount": len(sales_rows),
                "salesReceivedAmount": _number(sales_amount),
                "needsReviewCount": review_counts.get(row.id, 0),
            },
            "possibleDuplicateCount": len(_possible_duplicates(row, duplicate_map, duplicate_decisions)),
        })
    items.sort(
        key=lambda row: (
            -max(
                row["summary"]["purchaseAmount"],
                row["summary"]["invoiceAmount"],
                row["summary"]["bankPaidAmount"],
                row["summary"]["salesReceivedAmount"],
            ),
            row["name"],
        )
    )
    return {"total": len(items), "items": items[offset: offset + limit]}


def create_partner(db: Session, payload: dict[str, Any]) -> BusinessPartner:
    ctx = SyncContext(db)
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("往来单位名称不能为空")
    bank_accounts = _normalized_bank_accounts(payload)
    primary = _primary_bank_account(bank_accounts)
    resolution = ctx.index.resolve(
        name=name,
        tax_no=str(payload.get("tax_no") or ""),
        account_no=str(primary.get("account_no") or ""),
    )
    if resolution.partner is not None:
        raise ValueError(f"已存在往来单位「{resolution.partner.name}」，请在原档案补充资料")
    if resolution.candidates:
        raise ValueError("存在相似或冲突的往来单位，请先在待确认记录中处理")
    partner = _create_partner(
        ctx,
        name=name,
        tax_no=str(payload.get("tax_no") or ""),
        account_no=str(primary.get("account_no") or ""),
        account_name=str(primary.get("account_name") or ""),
        bank_name=str(primary.get("bank_name") or ""),
        roles=_roles(payload.get("roles") or ["counterparty"]),
        source="manual",
        contact=str(payload.get("contact") or ""),
        phone=str(payload.get("phone") or ""),
        address=str(payload.get("address") or ""),
        notes=str(payload.get("notes") or ""),
    )
    partner.bank_accounts = bank_accounts
    for account in bank_accounts:
        _add_identifier(
            ctx,
            partner,
            kind="bank_account",
            value=account["account_no"],
            source="manual",
            is_primary=bool(account["is_primary"]),
        )
    for former_name in payload.get("former_names") or []:
        _add_identifier(ctx, partner, kind="former_name", value=str(former_name), source="manual")
    return partner


def update_partner(db: Session, partner_id: int, payload: dict[str, Any]) -> BusinessPartner:
    partner = db.get(BusinessPartner, partner_id)
    if partner is None or partner.status == "archived":
        raise ValueError("往来单位不存在")
    ctx = SyncContext(db)
    new_name = str(payload.get("name") or "").strip()
    if not new_name:
        raise ValueError("往来单位名称不能为空")
    if normalize_name(new_name) != partner.normalized_name:
        _add_identifier(ctx, partner, kind="alias", value=partner.name, source="manual")
        partner.name = new_name
        partner.normalized_name = normalize_name(new_name)
        _add_identifier(ctx, partner, kind="name", value=new_name, source="manual", is_primary=True)
        ctx.index.add_identifier(partner.id, "name", partner.normalized_name)

    partner.contact = str(payload.get("contact") or "").strip()
    partner.phone = str(payload.get("phone") or "").strip()
    partner.address = str(payload.get("address") or "").strip()
    partner.notes = str(payload.get("notes") or "").strip()
    partner.roles = _roles(payload.get("roles") or ["counterparty"])

    tax_no = normalize_tax_no(payload.get("tax_no"))
    partner.tax_no = tax_no
    _add_identifier(ctx, partner, kind="tax_no", value=tax_no, source="manual", is_primary=True)

    if payload.get("bank_accounts") is not None:
        bank_accounts = _normalized_bank_accounts(payload)
    else:
        # 旧客户端仍可用单一银行字段更新主账户。
        bank_accounts = _normalized_bank_accounts(payload)
        if not bank_accounts and isinstance(partner.bank_accounts, list):
            bank_accounts = list(partner.bank_accounts)

    primary = _primary_bank_account(bank_accounts)
    partner.bank_accounts = bank_accounts
    partner.bank_name = str(primary.get("bank_name") or "")
    partner.bank_account_no = normalize_account(primary.get("account_no"))
    partner.bank_account_name = str(primary.get("account_name") or "")
    _replace_manual_identifiers(
        db,
        partner,
        kind="bank_account",
        values=[str(row["account_no"]) for row in bank_accounts],
        primary_value=partner.bank_account_no,
    )

    if payload.get("former_names") is not None:
        former_names = [
            str(value).strip()
            for value in (payload.get("former_names") or [])
            if normalize_name(value) and normalize_name(value) != partner.normalized_name
        ]
        _replace_manual_identifiers(
            db,
            partner,
            kind="former_name",
            values=former_names,
        )
    return partner


def add_identifier(db: Session, partner_id: int, *, kind: str, value: str) -> BusinessPartnerIdentifier:
    partner = db.get(BusinessPartner, partner_id)
    if partner is None or partner.status == "archived":
        raise ValueError("往来单位不存在")
    ctx = SyncContext(db)
    if kind not in IDENTIFIER_KINDS:
        raise ValueError("识别字段只能是名称别名、税号、银行账号或客户编码")
    if not normalize_identifier(kind, value):
        raise ValueError("识别字段不能为空")
    _add_identifier(ctx, partner, kind=kind, value=value, source="manual")
    if kind == "tax_no" and not partner.tax_no:
        partner.tax_no = normalize_tax_no(value)
    if kind == "bank_account" and not partner.bank_account_no:
        partner.bank_account_no = normalize_account(value)
    row = ctx.identifier_by_key[(partner.id, kind, normalize_identifier(kind, value))]
    return row


def _merge_partner_into(
    db: Session,
    *,
    target: BusinessPartner,
    source: BusinessPartner,
    actor: str = "",
    note: str = "",
) -> dict[str, Any]:
    """把重复主档并入唯一主体；原始业务字段不改写，被并档主体仅归档。"""
    target_tax = normalize_tax_no(target.tax_no)
    source_tax = normalize_tax_no(source.tax_no)
    if target_tax and source_tax and target_tax != source_tax:
        raise ValueError(
            f"两个档案税号冲突（{target_tax} / {source_tax}），不能直接合并"
        )

    ctx = SyncContext(db)
    _add_identifier(ctx, target, kind="former_name", value=source.name, source="master_merge")

    source_identifiers = (
        db.query(BusinessPartnerIdentifier)
        .filter(BusinessPartnerIdentifier.partner_id == source.id)
        .all()
    )
    for item in source_identifiers:
        kind = "former_name" if item.kind == "name" else item.kind
        if kind not in IDENTIFIER_KINDS:
            continue
        _add_identifier(
            ctx,
            target,
            kind=kind,
            value=item.value,
            source="master_merge",
            is_primary=False,
        )

    target.roles = _roles([*(target.roles or []), *(source.roles or [])])
    if not target.tax_no and source.tax_no:
        target.tax_no = source.tax_no
    if not target.contact and source.contact:
        target.contact = source.contact
    if not target.phone and source.phone:
        target.phone = source.phone
    if not target.address and source.address:
        target.address = source.address
    if not target.notes and source.notes:
        target.notes = source.notes

    target_accounts = _bank_accounts_payload(db, target)
    source_accounts = _bank_accounts_payload(db, source)
    combined: dict[str, dict[str, Any]] = {}
    for item in [*target_accounts, *source_accounts]:
        normalized = normalize_account(item.get("accountNo"))
        if not normalized:
            continue
        current = combined.get(normalized)
        if current is None:
            combined[normalized] = {
                "bank_name": item.get("bankName") or "",
                "account_no": normalized,
                "account_name": item.get("accountName") or "",
                "is_primary": bool(item.get("isPrimary")),
            }
        else:
            current["bank_name"] = current["bank_name"] or item.get("bankName") or ""
            current["account_name"] = current["account_name"] or item.get("accountName") or ""
            current["is_primary"] = bool(current["is_primary"] or item.get("isPrimary"))

    merged_accounts = list(combined.values())
    if merged_accounts:
        current_primary = normalize_account(target.bank_account_no)
        primary_index = next(
            (
                index for index, item in enumerate(merged_accounts)
                if current_primary and normalize_account(item["account_no"]) == current_primary
            ),
            next((index for index, item in enumerate(merged_accounts) if item["is_primary"]), 0),
        )
        for index, item in enumerate(merged_accounts):
            item["is_primary"] = index == primary_index
        primary = merged_accounts[primary_index]
        target.bank_accounts = merged_accounts
        target.bank_name = str(primary.get("bank_name") or "")
        target.bank_account_no = normalize_account(primary.get("account_no"))
        target.bank_account_name = str(primary.get("account_name") or "")
        _replace_manual_identifiers(
            db,
            target,
            kind="bank_account",
            values=[str(item["account_no"]) for item in merged_accounts],
            primary_value=target.bank_account_no,
        )

    for supplier in db.query(Supplier).filter(Supplier.partner_id == source.id).all():
        supplier.partner_id = target.id
    if target.legacy_supplier_id is None and source.legacy_supplier_id is not None:
        target.legacy_supplier_id = source.legacy_supplier_id
    source.legacy_supplier_id = None

    target_roles = {
        row.role: row
        for row in db.query(BusinessPartnerRole)
        .filter(BusinessPartnerRole.partner_id == target.id)
        .all()
    }
    for row in (
        db.query(BusinessPartnerRole)
        .filter(BusinessPartnerRole.partner_id == source.id)
        .all()
    ):
        if row.role in target_roles:
            db.delete(row)
        else:
            row.partner_id = target.id
            target_roles[row.role] = row

    target_banks = {
        row.normalized_account_no: row
        for row in db.query(BusinessPartnerBankAccount)
        .filter(BusinessPartnerBankAccount.partner_id == target.id)
        .all()
    }
    for row in (
        db.query(BusinessPartnerBankAccount)
        .filter(BusinessPartnerBankAccount.partner_id == source.id)
        .all()
    ):
        existing = target_banks.get(row.normalized_account_no)
        if existing is None:
            row.partner_id = target.id
            target_banks[row.normalized_account_no] = row
            continue
        existing.bank_name = existing.bank_name or row.bank_name
        existing.account_name = existing.account_name or row.account_name
        existing.verified = bool(existing.verified or row.verified)
        existing.status = "active" if "active" in {existing.status, row.status} else existing.status
        db.delete(row)

    from app.services.partner_reference_service import (
        materialize_partner_references,
        repoint_partner_references,
    )

    moved = repoint_partner_references(
        db,
        from_partner_id=source.id,
        to_partner_id=target.id,
    )

    source.status = "archived"
    merge_note = f"已合并至往来主体 #{target.id} {target.name}"
    if note:
        merge_note += f"；{note}"
    source.notes = (source.notes + "\n" + merge_note).strip() if source.notes else merge_note

    db.flush()
    materialized = materialize_partner_references(db)
    return {
        "targetPartnerId": target.id,
        "archivedPartnerId": source.id,
        "movedFacts": int(moved.get("moved", 0)),
        "movedBySource": moved.get("bySource", {}),
        "materializedRefs": int(materialized.get("materialized", 0)),
        "actor": str(actor or ""),
    }


def decide_duplicate(
    db: Session,
    partner_id: int,
    *,
    other_partner_id: int,
    same: bool,
    note: str = "",
    actor: str = "",
) -> dict[str, Any]:
    """人工判断两个档案是否同一真实主体。

    same=True 会执行真正的主档合并：业务表 direct partner FK、审计 links、
    Supplier 画像、角色和银行账户全部归到 partner_id；other_partner_id 仅归档保留。
    same=False 只记录“不是同一主体”的人工结论。
    """
    partner = db.get(BusinessPartner, partner_id)
    other = db.get(BusinessPartner, other_partner_id)
    if partner is None or partner.status == "archived":
        raise ValueError("往来单位不存在")
    if other is None or other.status == "archived":
        raise ValueError("对方往来单位不存在或已归档")
    if partner.id == other.id:
        raise ValueError("不能把同一个档案判断为疑似重复")

    decision = "same" if same else "different"
    clean_note = str(note or "").strip()
    decided_by = str(actor or "").strip()

    merge_result: dict[str, Any] | None = None
    if same:
        # 先做税号冲突等安全检查；失败时不写任何 review 结论。
        merge_result = _merge_partner_into(
            db,
            target=partner,
            source=other,
            actor=actor,
            note=clean_note,
        )

    existing = {
        (row.partner_id, row.other_partner_id): row
        for row in db.query(BusinessPartnerDuplicateReview)
        .filter(
            BusinessPartnerDuplicateReview.partner_id.in_([partner.id, other.id]),
            BusinessPartnerDuplicateReview.other_partner_id.in_([partner.id, other.id]),
        )
        .all()
    }
    for left, right in ((partner.id, other.id), (other.id, partner.id)):
        row = existing.get((left, right))
        if row is None:
            db.add(
                BusinessPartnerDuplicateReview(
                    partner_id=left,
                    other_partner_id=right,
                    decision=decision,
                    note=clean_note,
                    decided_by=decided_by,
                )
            )
        else:
            row.decision = decision
            row.note = clean_note
            row.decided_by = decided_by

    db.flush()
    detail = partner_detail(db, partner_id) or {}
    if merge_result is not None:
        detail["mergeResult"] = merge_result
    return detail

def claim_review_link(db: Session, *, partner_id: int, link_id: int, note: str = "") -> BusinessPartnerLink:
    partner = db.get(BusinessPartner, partner_id)
    link = db.get(BusinessPartnerLink, link_id)
    if partner is None or partner.status == "archived":
        raise ValueError("往来单位不存在")
    if link is None or link.status != "needs_review":
        raise ValueError("待确认记录不存在或已处理")
    candidates = {int(value) for value in (link.candidate_partner_ids or [])}
    if candidates and partner.id not in candidates:
        raise ValueError("该记录不属于当前往来单位的候选范围")
    ctx = SyncContext(db)
    link.partner_id = partner.id
    link.status = "linked"
    link.match_method = "manual"
    link.confidence = 1.0
    link.confirmed = True
    link.candidate_partner_ids = []
    link.note = str(note or "").strip()
    _apply_identity(
        ctx,
        partner,
        name=link.raw_name,
        tax_no=link.raw_tax_no,
        account_no=link.raw_account_no,
        roles=["supplier" if link.relation_role in {"supplier", "seller"} else "customer" if link.relation_role in {"customer", "buyer"} else "counterparty"],
        source="manual",
    )
    # 人工确认必须立即写回业务事实的 canonical FK，不能等下一次“核对全部来源”。
    from app.services.partner_reference_service import materialize_partner_references

    materialize_partner_references(db)
    return link
