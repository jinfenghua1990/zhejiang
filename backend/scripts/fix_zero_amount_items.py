# -*- coding: utf-8 -*-
"""回填吉客云入库单「零金额明细」：amount_tax = unit_price_tax × quantity。

协议依据（2026-09-05 排查确认）：
  吉客云同步的入库单 head.total_amount 是真实总额，但部分明细行 amount_tax=0（占位）。
  对全部 11 张异常单验证：Σ(明细 unit_price_tax × quantity) == head.total_amount 分毫不差
  ⇒ 单价列是吉客云真实使用值，仅金额列缺失。按 单价×数量 回填即恢复协议不变量
  head = Σ amount_tax，无任何猜测成分。

规则：
  - 只处理 amount_tax=0 且 unit_price_tax>0 且 quantity>0 的明细（数量金额都有才填）
  - 回填后 amount_notax 同值；raw 含税金额 同步更新
  - 单据 head 用 recalc_document_amount 重算（Σ==head 时不变，保底一致）
  - 幂等：已一致的行跳过

用法：python backend/scripts/fix_zero_amount_items.py [--apply]
"""
import sys
from decimal import Decimal, ROUND_HALF_UP

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.audit import audit
from app.db import SessionLocal
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.services.inbound_allocation_seed import recalc_document_amount

APPLY = "--apply" in sys.argv
ACTOR = "admin"
Q4 = Decimal("0.0001")


def q4(v: Decimal) -> Decimal:
    return v.quantize(Q4, rounding=ROUND_HALF_UP)


def main() -> None:
    db = SessionLocal()
    items = (
        db.query(JackyunGoodsDocumentItem, JackyunGoodsDocument)
        .join(JackyunGoodsDocument, JackyunGoodsDocumentItem.document_id == JackyunGoodsDocument.id)
        .filter(JackyunGoodsDocument.document_type == "inbound")
        .all()
    )
    # 单据级聚合：判断 head == Σ非零金额 + Σ零金额行(单价×数量) 才允许回填。
    # 若 Σ已有金额本来就等于 head，零金额行是真实赠品，回填会凭空加价 —— 必须排除。
    zero_rows: dict[int, list] = {}   # doc.id -> [(item, doc)]
    doc_head: dict[int, Decimal] = {}
    doc_sum_amt: dict[int, Decimal] = {}
    doc_sum_uq: dict[int, Decimal] = {}
    for item, doc in items:
        amt = Decimal(str(item.amount_tax or 0))
        unit = Decimal(str(item.unit_price_tax or 0))
        qty = Decimal(str(item.quantity or 0))
        doc_head[doc.id] = Decimal(str(doc.total_amount or 0))
        if amt != 0:
            doc_sum_amt[doc.id] = doc_sum_amt.get(doc.id, Decimal(0)) + amt
        elif unit > 0 and qty > 0:
            zero_rows.setdefault(doc.id, []).append((item, doc))
            doc_sum_uq[doc.id] = doc_sum_uq.get(doc.id, Decimal(0)) + unit * qty
        else:
            doc_sum_amt[doc.id] = doc_sum_amt.get(doc.id, Decimal(0))  # 无单价，无法回填

    plan, touched_docs = [], {}
    for doc_id, rows in sorted(zero_rows.items()):
        head, s_amt, s_uq = doc_head[doc_id], doc_sum_amt.get(doc_id, Decimal(0)), doc_sum_uq[doc_id]
        if abs(head - s_amt - s_uq) > Decimal("0.01"):
            continue  # 单价×数量 与 head 差值不吻合（多为赠品行），不回填
        for item, doc in rows:
            new_amt = q4(Decimal(str(item.unit_price_tax)) * Decimal(str(item.quantity)))
            plan.append((item, doc, Decimal(str(item.amount_tax or 0)), new_amt))
        touched_docs[doc_id] = doc

    print(f"待回填明细 {len(plan)} 条，涉及单据 {len(touched_docs)} 张"
          f"（赠品等不吻合单据已排除 {len(zero_rows) - len(touched_docs)} 张）")
    by_doc: dict[int, list] = {}
    for item, doc, old, new in plan:
        by_doc.setdefault(doc.id, []).append((item, old, new))
    for doc_id in sorted(by_doc):
        doc = touched_docs[doc_id]
        total = sum(new for _, _, new in by_doc[doc_id])
        print(f"  doc#{doc_id} {doc.goodsdoc_no}: head={doc.total_amount} 回填Σ={total} "
              f"({len(by_doc[doc_id])} 行)")
        for item, old, new in by_doc[doc_id]:
            print(f"     #{item.id} {item.goods_no} unit={item.unit_price_tax} "
                  f"qty={item.quantity} amt {old} -> {new}")

    if not APPLY:
        print("\n(dry-run，未写库；加 --apply 执行)")
        db.close()
        return

    for item, doc, old, new in plan:
        item.amount_tax = new
        item.amount_notax = new
        item.raw = dict(item.raw or {}, 含税金额=str(new))
        if item.match_status not in ("manual",):
            item.match_status = "manual_adjust"
        item.match_note = (f"零金额明细回填：单价×数量={new}（head 自证：Σ 单价×数量 == 单据总额）")
        db.add(item)
    db.commit()

    audit(db, ACTOR, "jackyun.inbound.fill_zero_amount", "jackyun_goods_document_items",
          detail={"filled": len(plan), "documents": sorted(by_doc),
                  "basis": "Σ(unit×qty)==head 分毫不差，回填零金额明细恢复 head=Σamount_tax 协议"})
    db.commit()

    for doc_id in sorted(by_doc):
        res = recalc_document_amount(db, doc_id, actor=ACTOR)
        print(f"  head 重算 doc#{doc_id}: {res['before']} -> {res['after']}")
    print(f"\n完成：回填 {len(plan)} 条明细。")
    db.close()


if __name__ == "__main__":
    main()
