from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def _text(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def test_procurement_domain_does_not_write_tax_invoice_state_directly():
    source = _text("backend/app/services/procurement_chain_service.py")
    assert "invoice.match_status =" not in source
    assert "invoice.verified =" not in source
    assert "invoice.payment_method =" not in source
    assert "mark_invoice_linked" not in source


def test_tax_invoice_domain_exposes_canonical_state_mutators():
    source = _text("backend/app/services/tax_invoice_service.py")
    assert "def sync_business_match_status(" in source
    assert "def set_invoice_verified(" in source

def test_procurement_auto_invoice_links_must_store_allocated_amount():
    source = _text("backend/app/services/procurement_chain_service.py")
    assert "allocated_amount=allocated_amount" in source
    assert "TaxInvoiceLink.confirmed.is_(True)" in source


def test_bank_reconciliation_domain_does_not_write_invoice_manual_payment_fact():
    for relative in (
        "backend/app/services/payment_invoice_match_service.py",
        "backend/app/services/payment_invoice_match_helpers.py",
    ):
        source = _text(relative)
        assert "invoice.payment_method =" not in source
        assert "inv.payment_method =" not in source
        assert "_clear_manual_personal_on_bank_evidence" not in source
