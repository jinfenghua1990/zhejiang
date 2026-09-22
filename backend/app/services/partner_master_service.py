"""Top-level BusinessPartner V2 rebuild orchestration."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.services import business_partner_service
from app.services import payment_invoice_match_service
from app.services.partner_reference_service import partner_reference_coverage
from app.services.supplier_sync_service import sync_suppliers_from_business_data


def rebuild_partner_master(db: Session, *, actor: str = "system", run_payment_match: bool = True) -> dict[str, Any]:
    """Rebuild canonical identity from source facts without rewriting raw source fields.

    Order matters:
    1. materialize any missing legacy Supplier profiles from real procurement facts;
    2. resolve all source facts into BusinessPartner + direct partner FKs;
    3. optionally reconcile bank payments to invoices using the canonical identities;
    4. resolve once more because a confirmed payment link can itself be identity evidence.
    """
    supplier_sync = sync_suppliers_from_business_data(db)
    first = business_partner_service.sync_business_partners(db)

    payment: dict[str, Any] = {
        "periodCount": 0,
        "matchedLinks": 0,
        "repaired": 0,
        "ambiguous": 0,
        "periods": [],
    }
    if run_payment_match:
        payment = payment_invoice_match_service.auto_match_all_periods(db, actor=actor)

    second = business_partner_service.sync_business_partners(db)
    coverage = partner_reference_coverage(db)

    return {
        "createdPartners": int(first.get("createdPartners", 0)) + int(second.get("createdPartners", 0)),
        "updatedPartners": int(first.get("updatedPartners", 0)) + int(second.get("updatedPartners", 0)),
        "createdLinks": int(first.get("createdLinks", 0)) + int(second.get("createdLinks", 0)),
        "updatedLinks": int(first.get("updatedLinks", 0)) + int(second.get("updatedLinks", 0)),
        "materializedRefs": int(first.get("materializedRefs", 0)) + int(second.get("materializedRefs", 0)),
        "needsReview": int(second.get("needsReview", first.get("needsReview", 0))),
        "bankInvoicePeriods": int(payment.get("periodCount", 0)),
        "bankInvoiceMatchesCreated": int(payment.get("matchedLinks", 0)),
        "bankInvoiceRepaired": int(payment.get("repaired", 0)),
        "bankInvoiceAmbiguous": int(payment.get("ambiguous", 0)),
        "paymentPeriods": list(payment.get("periods", [])),
        "supplierSync": supplier_sync,
        "coverage": coverage,
    }
