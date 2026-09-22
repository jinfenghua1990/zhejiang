"""一次性迁移：tax_invoices.category 旧四值 + processing_status → v2 6+1 分类。

映射规则（用户钦定）：
- 货款发票 goods_payment → goods（运营成本：货款发票）
- 平台服务类型发票 platform_service → platform_fee（运营成本：平台服务费）
- 报销类型发票 reimbursement → reimburse_operating（报销：运营成本）
- 未知 unknown → 空（待判断）
- category 为空的历史行按旧 processing_status 映射：required→goods、not_required→reimburse_operating、pending→空

迁移后按 v2 分类重算 processing_status（运营成本三分类→required；报销两分类与 excluded→not_required；空→pending），
并写一条审计日志 action=tax_invoice.category_v2_migrate。幂等可重跑。
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.audit import audit  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models.tax import TaxInvoice  # noqa: E402
from app.services.tax_invoice_service import (  # noqa: E402
    CATEGORY_V2_KEYS,
    _derive_processing_status,
    category_group,
)

CATEGORY_MAP = {
    "goods_payment": "goods",
    "platform_service": "platform_fee",
    "reimbursement": "reimburse_operating",
    "unknown": "",
}
EMPTY_STATUS_MAP = {"required": "goods", "not_required": "reimburse_operating", "pending": ""}


def _distribution(rows: list[TaxInvoice]) -> Counter:
    return Counter(
        (row.category or "(空待判断)", category_group(row.category), row.processing_status)
        for row in rows
    )


def main() -> None:
    db = SessionLocal()
    try:
        rows = db.query(TaxInvoice).all()
        before = _distribution(rows)
        changed = 0
        for row in rows:
            old_category = row.category or ""
            if old_category in CATEGORY_MAP:
                new_category = CATEGORY_MAP[old_category]
            elif old_category == "":
                new_category = EMPTY_STATUS_MAP.get(row.processing_status or "pending", "")
            else:
                new_category = old_category  # 已是 v2 key，保持不变
            if new_category != old_category or row.processing_status != _derive_processing_status(new_category):
                changed += 1
            row.category = new_category
            row.processing_status = _derive_processing_status(new_category)
        db.commit()

        db.expire_all()
        after_rows = db.query(TaxInvoice).all()
        after = _distribution(after_rows)

        print("=== 迁移前分布 (category, group, processing_status) ===")
        for key, count in sorted(before.items()):
            print(f"  {key}: {count}")
        print(f"变更行数: {changed}")
        print("=== 迁移后分布 (category, group, processing_status) ===")
        for key, count in sorted(after.items()):
            print(f"  {key}: {count}")

        unexpected = {row.category for row in after_rows if row.category and row.category not in CATEGORY_V2_KEYS}
        if unexpected:
            raise SystemExit(f"发现非法 category 残留: {unexpected}")

        audit(db, "system", "tax_invoice.category_v2_migrate", "tax_invoices", "", {
            "changed": changed,
            "total": len(after_rows),
            "before": {"/".join(k): v for k, v in before.items()},
            "after": {"/".join(k): v for k, v in after.items()},
        })
        print("审计日志已写入：tax_invoice.category_v2_migrate")
    finally:
        db.close()


if __name__ == "__main__":
    main()
