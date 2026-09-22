# -*- coding: utf-8 -*-
"""采购内容自动确认：对「配平 + 无固定成本异常」的待完善订单自动 refine。

用户口径：只要数据确定没问题就应该自动确认（本应内置的机制，之前缺失）。
守卫（必须同时满足才自动确认，任一不满足则跳过并说明原因）：
  1) PO 状态 = pending_refine
  2) 配平：Σ分配 == 实付 + 微调（unallocated == 0）
  3) 固定成本校验无异常（cost_policy.allocation_cost_anomalies 为空）
  4) 至少有一条采购内容分配行
不触碰入库单 / 链路 / SKU 成本档案。

用法：python backend/scripts/auto_confirm_refined.py [--apply]
"""
import sys
from decimal import Decimal

from sqlalchemy import text

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.audit import audit
from app.db import SessionLocal
from app.models.purchase import ExternalPurchaseOrder
from app.services.cost_policy import allocation_cost_anomalies
from app.services.purchase_service import mark_refined

APPLY = "--apply" in sys.argv
ACTOR = "admin"


def main() -> None:
    db = SessionLocal()
    rows = list(db.execute(text(
        "select po.id, po.external_order_id, po.purchase_status, po.paid_amount, "
        "po.adjustment_amount, "
        "coalesce((select sum(amount) from purchase_allocation_items a where a.po_id=po.id),0) "
        "as alloc, (select count(*) from purchase_allocation_items a where a.po_id=po.id) as cnt "
        "from external_purchase_orders po where po.purchase_status='pending_refine' order by po.id")))

    ready, skipped = [], []
    for r in rows:
        po_id, order_no, _, paid, adj, alloc, cnt = (
            r[0], r[1], r[2], Decimal(str(r[3] or 0)), Decimal(str(r[4] or 0)),
            Decimal(str(r[5])), r[6])
        target = paid + adj
        gap = alloc - target
        if cnt == 0:
            skipped.append((po_id, order_no, "无采购内容分配行"))
            continue
        if abs(gap) > Decimal("0.0001"):
            skipped.append((po_id, order_no, f"未配平（差 {gap}）"))
            continue
        po = db.get(ExternalPurchaseOrder, po_id)
        anomalies = allocation_cost_anomalies(db, po)
        if anomalies:
            skipped.append((po_id, order_no, f"固定成本异常：{anomalies[0]}"))
            continue
        ready.append((po_id, order_no, alloc))

    print(f"可自动确认 {len(ready)} 单 / 跳过 {len(skipped)} 单")
    print("\n--- 将确认 ---")
    for po_id, order_no, alloc in ready:
        print(f"  po#{po_id} {order_no}  采购内容金额 {alloc}")
    print("\n--- 跳过 ---")
    for po_id, order_no, why in skipped:
        print(f"  po#{po_id} {order_no}: {why}")

    if not APPLY:
        print("\n(dry-run，未写库；加 --apply 执行)")
        db.close()
        return

    ok = fail = 0
    for po_id, order_no, alloc in ready:
        po = db.get(ExternalPurchaseOrder, po_id)
        try:
            mark_refined(db, po, actor=ACTOR)
            audit(db, ACTOR, "purchase.po.auto_refined", "external_purchase_orders", po_id,
                  {"externalOrderId": order_no, "goods": str(alloc), "basis": "配平且无成本异常，自动确认"})
            ok += 1
        except ValueError as exc:
            fail += 1
            print(f"  ✗ po#{po_id} 失败：{exc}")
    db.commit()
    print(f"\n已自动确认 {ok} 单，失败 {fail} 单。")
    db.close()


if __name__ == "__main__":
    main()
