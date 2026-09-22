"""统一往来单位数据健康检查。

用于发现采购、发票、付款等业务事实没有收敛到 BusinessPartner 的情况。
不修改业务数据，只提供检查结果。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.business_partner import BusinessPartnerLink
from app.models.purchase import Supplier, ExternalPurchaseOrder, JackyunPurchaseOrder
from app.models.tax import TaxInvoice
from app.models.bank import BankTransaction


def partner_health_report(db: Session) -> dict[str, Any]:
    """返回统一往来单位覆盖情况。"""

    checks = {
        "suppliers": {
            "total": db.query(Supplier).count(),
            "unlinked": db.query(Supplier).filter(Supplier.partner_id.is_(None)).count(),
        },
        "externalPurchases": {
            "total": db.query(ExternalPurchaseOrder).count(),
            "unlinked": db.query(ExternalPurchaseOrder)
            .filter(ExternalPurchaseOrder.supplier_partner_id.is_(None))
            .count(),
        },
        "jackyunPurchases": {
            "total": db.query(JackyunPurchaseOrder).count(),
            "unlinked": db.query(JackyunPurchaseOrder)
            .filter(JackyunPurchaseOrder.supplier_partner_id.is_(None))
            .count(),
        },
        "taxInvoices": {
            "total": db.query(TaxInvoice).count(),
            "unlinked": db.query(TaxInvoice)
            .filter(TaxInvoice.seller_partner_id.is_(None))
            .count(),
        },
        "bankTransactions": {
            "total": db.query(BankTransaction).count(),
            "unlinked": db.query(BankTransaction)
            .filter(BankTransaction.counterparty_partner_id.is_(None))
            .count(),
        },
        "reviewLinks": {
            "total": db.query(BusinessPartnerLink).count(),
            "needsReview": db.query(BusinessPartnerLink)
            .filter(BusinessPartnerLink.status == "needs_review")
            .count(),
        },
    }

    total = sum(row["total"] for row in checks.values())
    unlinked = sum(row.get("unlinked", row.get("needsReview", 0)) for row in checks.values())

    return {
        "checks": checks,
        "totalFacts": total,
        "unlinkedFacts": unlinked,
        "coverage": round((total - unlinked) / total, 4) if total else 1,
    }
