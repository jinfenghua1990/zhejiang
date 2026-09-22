"""BusinessPartner V2 direct-reference materialization.

BusinessPartnerLink remains the auditable resolution trail. Once a source is resolved as
linked, the corresponding operational fact stores the canonical partner FK as its normal
query path. Raw names/tax numbers/accounts are never overwritten.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.models.alibaba1688_import import Alibaba1688Order
from app.models.bank import BankTransaction
from app.models.business_partner import (
    BusinessPartner,
    BusinessPartnerBankAccount,
    BusinessPartnerIdentifier,
    BusinessPartnerLink,
    BusinessPartnerRole,
)
from app.models.consumable_purchase import ConsumablePurchase
from app.models.jackyun import JackyunGoodsDocument, JackyunPurchaseReturn, JackyunPurchaseSettlement
from app.models.jky_web import JkyWebSalesOrder, JkyWebStockinOrder
from app.models.purchase import ExternalPurchaseOrder, JackyunPurchaseOrder, Supplier
from app.models.sales import SalesOrder
from app.models.tax import TaxInvoice


ROLE_VALUES = {"supplier", "customer", "counterparty"}


SOURCE_BINDINGS: dict[tuple[str, str], tuple[type[Any], str]] = {
    ("supplier", "supplier"): (Supplier, "partner_id"),
    ("external_purchase_order", "supplier"): (ExternalPurchaseOrder, "supplier_partner_id"),
    ("alibaba1688_order", "supplier"): (Alibaba1688Order, "supplier_partner_id"),
    ("jackyun_purchase_order", "supplier"): (JackyunPurchaseOrder, "supplier_partner_id"),
    ("jackyun_purchase_settlement", "supplier"): (JackyunPurchaseSettlement, "supplier_partner_id"),
    ("jackyun_purchase_return", "supplier"): (JackyunPurchaseReturn, "supplier_partner_id"),
    ("consumable_purchase", "supplier"): (ConsumablePurchase, "supplier_partner_id"),
    ("inbound_document", "supplier"): (JackyunGoodsDocument, "supplier_partner_id"),
    ("jky_web_stockin_order", "supplier"): (JkyWebStockinOrder, "supplier_partner_id"),
    ("tax_invoice", "seller"): (TaxInvoice, "seller_partner_id"),
    ("tax_invoice", "buyer"): (TaxInvoice, "buyer_partner_id"),
    ("bank_transaction", "counterparty"): (BankTransaction, "counterparty_partner_id"),
    ("jky_web_sales_order", "customer"): (JkyWebSalesOrder, "customer_partner_id"),
}


def normalize_account(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[\s-]+", "", unicodedata.normalize("NFKC", str(value))).upper()


def _sync_roles(db: Session) -> int:
    existing = {
        (int(row.partner_id), row.role): row
        for row in db.query(BusinessPartnerRole).all()
    }
    changed = 0
    for partner in db.query(BusinessPartner).filter(BusinessPartner.status == "active").all():
        for role in sorted(set(partner.roles or []) & ROLE_VALUES):
            key = (int(partner.id), role)
            if key in existing:
                continue
            row = BusinessPartnerRole(
                partner_id=partner.id,
                role=role,
                source="partner_master",
                confirmed=True,
            )
            db.add(row)
            existing[key] = row
            changed += 1
    return changed


def _bank_account_candidates(db: Session) -> dict[int, dict[str, dict[str, Any]]]:
    desired: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)

    for partner in db.query(BusinessPartner).filter(BusinessPartner.status == "active").all():
        raw_rows = partner.bank_accounts if isinstance(partner.bank_accounts, list) else []
        for raw in raw_rows:
            if not isinstance(raw, dict):
                continue
            account_no = str(raw.get("account_no") or raw.get("accountNo") or "").strip()
            normalized = normalize_account(account_no)
            if not normalized:
                continue
            desired[int(partner.id)][normalized] = {
                "bank_name": str(raw.get("bank_name") or raw.get("bankName") or "").strip(),
                "account_no": account_no,
                "account_name": str(raw.get("account_name") or raw.get("accountName") or "").strip(),
                "is_primary": bool(raw.get("is_primary") if "is_primary" in raw else raw.get("isPrimary")),
                "source": "partner_master",
                "verified": True,
            }

        primary_normalized = normalize_account(partner.bank_account_no)
        if primary_normalized and primary_normalized not in desired[int(partner.id)]:
            desired[int(partner.id)][primary_normalized] = {
                "bank_name": partner.bank_name or "",
                "account_no": partner.bank_account_no or "",
                "account_name": partner.bank_account_name or "",
                "is_primary": True,
                "source": "partner_master",
                "verified": True,
            }

    identifiers = (
        db.query(BusinessPartnerIdentifier)
        .filter(BusinessPartnerIdentifier.kind == "bank_account")
        .all()
    )
    for identifier in identifiers:
        normalized = normalize_account(identifier.normalized_value or identifier.value)
        if not normalized:
            continue
        bucket = desired[int(identifier.partner_id)]
        bucket.setdefault(
            normalized,
            {
                "bank_name": "",
                "account_no": identifier.value,
                "account_name": "",
                "is_primary": bool(identifier.is_primary),
                "source": identifier.source or "identifier",
                "verified": identifier.source == "manual",
            },
        )
    return desired


def _sync_bank_accounts(db: Session) -> int:
    desired = _bank_account_candidates(db)
    existing = {
        (int(row.partner_id), row.normalized_account_no): row
        for row in db.query(BusinessPartnerBankAccount).all()
    }
    changed = 0

    for partner_id, accounts in desired.items():
        primary_seen = False
        for normalized, payload in accounts.items():
            key = (partner_id, normalized)
            row = existing.get(key)
            if row is None:
                row = BusinessPartnerBankAccount(
                    partner_id=partner_id,
                    normalized_account_no=normalized,
                    bank_name=payload["bank_name"],
                    account_no=payload["account_no"],
                    account_name=payload["account_name"],
                    is_primary=bool(payload["is_primary"] and not primary_seen),
                    status="active",
                    source=payload["source"],
                    verified=bool(payload["verified"]),
                )
                db.add(row)
                existing[key] = row
                changed += 1
            else:
                values = {
                    "bank_name": payload["bank_name"] or row.bank_name,
                    "account_no": payload["account_no"] or row.account_no,
                    "account_name": payload["account_name"] or row.account_name,
                    "is_primary": bool(payload["is_primary"] and not primary_seen),
                    "status": "active",
                    "verified": bool(row.verified or payload["verified"]),
                }
                for field, value in values.items():
                    if getattr(row, field) != value:
                        setattr(row, field, value)
                        changed += 1
            if row.is_primary:
                primary_seen = True

        # A manually maintained bank account that was removed from both master JSON and
        # identifiers becomes inactive rather than being deleted, preserving audit history.
        desired_keys = set(accounts)
        for (pid, normalized), row in existing.items():
            if pid != partner_id or normalized in desired_keys:
                continue
            if row.source in {"partner_master", "manual"} and row.status != "inactive":
                row.status = "inactive"
                row.is_primary = False
                changed += 1

    return changed


def repoint_partner_references(db: Session, *, from_partner_id: int, to_partner_id: int) -> dict[str, int]:
    """Move every direct operational FK from one partner to another.

    Used by canonical master merges. Raw source names/tax numbers/accounts are untouched;
    only the canonical identity reference changes.
    """
    if from_partner_id == to_partner_id:
        return {"moved": 0, "bySource": {}}

    bindings: list[tuple[str, type[Any], str]] = []
    seen: set[tuple[type[Any], str]] = set()
    for (source_type, _role), (model, attr) in SOURCE_BINDINGS.items():
        key = (model, attr)
        if key in seen:
            continue
        seen.add(key)
        bindings.append((source_type, model, attr))
    # Generic sales_orders has no BusinessPartnerLink source yet, but may already carry
    # a canonical customer FK from a normalized sales import.
    if (SalesOrder, "customer_partner_id") not in seen:
        bindings.append(("sales_order", SalesOrder, "customer_partner_id"))

    moved_by_source: dict[str, int] = {}
    for source_type, model, attr in bindings:
        column = getattr(model, attr)
        rows = db.query(model).filter(column == from_partner_id).all()
        if not rows:
            continue
        for row in rows:
            setattr(row, attr, to_partner_id)
        moved_by_source[source_type] = len(rows)

    # Audit links remain the evidence trail, but their canonical target must follow the merge.
    link_rows = (
        db.query(BusinessPartnerLink)
        .filter(BusinessPartnerLink.partner_id == from_partner_id)
        .all()
    )
    for row in link_rows:
        row.partner_id = to_partner_id
        if row.status == "linked":
            row.match_method = row.match_method or "master_merge"
    if link_rows:
        moved_by_source["business_partner_link"] = len(link_rows)

    db.flush()
    return {
        "moved": sum(moved_by_source.values()),
        "bySource": dict(sorted(moved_by_source.items())),
    }


def _sync_generic_sales_partner_refs(db: Session) -> int:
    """Propagate customer identity from JKY Web orders into normalized SalesOrder rows.

    Order/trade IDs are stable business identifiers. A key is usable only when every
    JKY row carrying it points to the same canonical partner; conflicting keys are ignored.
    Raw customer names are deliberately not used here.
    """
    key_partners: dict[str, set[int]] = defaultdict(set)
    for row in (
        db.query(JkyWebSalesOrder)
        .filter(JkyWebSalesOrder.customer_partner_id.isnot(None))
        .all()
    ):
        partner_id = int(row.customer_partner_id)
        for value in (row.trade_no, row.source_trade_no):
            key = str(value or "").strip()
            if key:
                key_partners[key].add(partner_id)

    stable = {
        key: next(iter(partner_ids))
        for key, partner_ids in key_partners.items()
        if len(partner_ids) == 1
    }
    if not stable:
        return 0

    changed = 0
    for row in db.query(SalesOrder).all():
        keys = [
            str(row.order_no or "").strip(),
            str(row.source_order_id or "").strip(),
            *[
                str(value or "").strip()
                for value in (row.identity_keys if isinstance(row.identity_keys, list) else [])
            ],
        ]
        candidates = {
            stable[key]
            for key in keys
            if key and key in stable
        }
        if len(candidates) != 1:
            continue
        partner_id = next(iter(candidates))
        if row.customer_partner_id != partner_id:
            row.customer_partner_id = partner_id
            changed += 1
    return changed


def materialize_partner_references(db: Session) -> dict[str, int]:
    """Copy linked audit relationships into source-table FKs; idempotent."""
    changed_by_source: dict[str, int] = defaultdict(int)
    rows = (
        db.query(BusinessPartnerLink)
        .filter(
            BusinessPartnerLink.status == "linked",
            BusinessPartnerLink.partner_id.isnot(None),
        )
        .all()
    )
    grouped: dict[tuple[str, str], list[BusinessPartnerLink]] = defaultdict(list)
    for row in rows:
        grouped[(row.source_type, row.relation_role)].append(row)

    for key, links in grouped.items():
        binding = SOURCE_BINDINGS.get(key)
        if binding is None:
            continue
        model, attr = binding
        ids = [int(link.source_id) for link in links]
        objects = {
            int(obj.id): obj
            for obj in db.query(model).filter(model.id.in_(ids)).all()
        }
        for link in links:
            obj = objects.get(int(link.source_id))
            if obj is None:
                continue
            partner_id = int(link.partner_id)
            if getattr(obj, attr) != partner_id:
                setattr(obj, attr, partner_id)
                changed_by_source[link.source_type] += 1

    # Legacy supplier profile can also be deterministically recovered from the partner row.
    for partner in db.query(BusinessPartner).filter(BusinessPartner.legacy_supplier_id.isnot(None)).all():
        supplier = db.get(Supplier, int(partner.legacy_supplier_id))
        if supplier is not None and supplier.partner_id != partner.id:
            supplier.partner_id = partner.id
            changed_by_source["supplier"] += 1

    sales_rows = _sync_generic_sales_partner_refs(db)
    if sales_rows:
        changed_by_source["sales_order"] += sales_rows

    role_rows = _sync_roles(db)
    bank_rows = _sync_bank_accounts(db)
    db.flush()
    return {
        "materialized": sum(changed_by_source.values()),
        "rolesCreated": role_rows,
        "bankAccountsChanged": bank_rows,
        "bySource": dict(sorted(changed_by_source.items())),
    }


def _coverage(
    db: Session,
    model: type[Any],
    attr: str,
    *eligibility,
) -> dict[str, Any]:
    """Coverage only counts facts that are expected to have an external party."""
    query = db.query(model)
    if eligibility:
        query = query.filter(*eligibility)
    total = query.count()
    column = getattr(model, attr)
    linked = query.filter(column.isnot(None)).count()
    return {
        "total": total,
        "linked": linked,
        "unlinked": max(0, total - linked),
        "coverage": round(linked / total, 4) if total else 1.0,
    }


def partner_reference_coverage(db: Session) -> dict[str, Any]:
    """Operational observability for the canonical identity rollout."""
    sources = {
        "suppliers": _coverage(db, Supplier, "partner_id"),
        "externalPurchases": _coverage(
            db, ExternalPurchaseOrder, "supplier_partner_id",
            ExternalPurchaseOrder.supplier_name != "",
        ),
        "alibaba1688": _coverage(
            db, Alibaba1688Order, "supplier_partner_id",
            Alibaba1688Order.row_status != "deleted",
        ),
        "jackyunPurchases": _coverage(
            db, JackyunPurchaseOrder, "supplier_partner_id",
            JackyunPurchaseOrder.supplier_name != "",
        ),
        "jackyunSettlements": _coverage(
            db, JackyunPurchaseSettlement, "supplier_partner_id",
            JackyunPurchaseSettlement.supplier_name != "",
        ),
        "jackyunReturns": _coverage(
            db, JackyunPurchaseReturn, "supplier_partner_id",
            JackyunPurchaseReturn.supplier_name != "",
        ),
        "inboundDocuments": _coverage(
            db, JackyunGoodsDocument, "supplier_partner_id",
            JackyunGoodsDocument.document_type == "inbound",
            JackyunGoodsDocument.supplier_name != "",
        ),
        "consumablePurchases": _coverage(
            db, ConsumablePurchase, "supplier_partner_id",
            ConsumablePurchase.supplier_name != "",
        ),
        "jkyStockin": _coverage(
            db, JkyWebStockinOrder, "supplier_partner_id",
            JkyWebStockinOrder.supplier_name != "",
        ),
        # 输入票的卖方、输出票的买方才是外部往来主体；自己的那一侧不计入覆盖率。
        "taxInvoiceSeller": _coverage(
            db, TaxInvoice, "seller_partner_id",
            TaxInvoice.direction == "input",
        ),
        "taxInvoiceBuyer": _coverage(
            db, TaxInvoice, "buyer_partner_id",
            TaxInvoice.direction == "output",
        ),
        # 利息/手续费等没有对方身份的银行行不是“漏匹配”。
        "bankTransactions": _coverage(
            db, BankTransaction, "counterparty_partner_id",
            (BankTransaction.counterparty_name != "") | (BankTransaction.counterparty_account != ""),
        ),
        "jkySales": _coverage(db, JkyWebSalesOrder, "customer_partner_id"),
    }
    total = sum(row["total"] for row in sources.values())
    linked = sum(row["linked"] for row in sources.values())
    return {
        "sources": sources,
        "totalFacts": total,
        "linkedFacts": linked,
        "unlinkedFacts": max(0, total - linked),
        "coverage": round(linked / total, 4) if total else 1.0,
    }
