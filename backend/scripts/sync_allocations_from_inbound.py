# -*- coding: utf-8 -*-
"""把采购分配行(allocation)的单价同步为「Excel 修正后」的入库明细真实单价。

背景：吉客云入库单价格被乱写（占位 1.0/10.0），用户以《入库申请单货品.xlsx》为准已修正
入库明细；本脚本把 PO 的采购内容分配行同步为真实单价，避免订单金额/成本仍用占位价。

匹配规则（PO 已确认关联的入库明细）：
  1) matched_sku_id 与分配行 sku_id 相同；
  2) 优先取 item.raw._1688采购订单 == PO.external_order_id 的行（一单多 PO 共用单据时精确对应）；
  3) 仍多条时用分配行数量对齐，取不到则跳过报告。
金额口径：
  - 数量一致 → 直接采用明细金额/单价；
  - 数量不一致 → 保留分配行数量，按明细单价重算金额（不动数量，防破坏配平）。
只改 pending_refine 的 PO；不触碰入库单/链路/SKU 成本档案。

用法：python backend/scripts/sync_allocations_from_inbound.py [--apply]
"""
import sys
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import text

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.audit import audit
from app.db import SessionLocal

Q4 = Decimal("0.0001")
APPLY = "--apply" in sys.argv
ACTOR = "admin"

SQL = text("""
select po.id as po_id, po.external_order_id, po.purchase_status, po.paid_amount,
       i.id as item_id, i.goods_no, i.goods_name, i.quantity, i.unit_price_tax,
       i.amount_tax, i.matched_sku_id, d.goodsdoc_no, i.raw
from procurement_chain_links l
join external_purchase_orders po
  on po.external_order_id = (select external_order_id from alibaba1688_orders a where a.id = l.order_id)
join jackyun_goods_document_items i on i.document_id = l.target_id
join jackyun_goods_documents d on d.id = i.document_id
where l.target_type = 'inbound' and l.confirmed = true
""")


def d(v) -> Decimal:
    try:
        return Decimal(str(v or 0))
    except Exception:
        return Decimal(0)


def q4(v: Decimal) -> Decimal:
    return v.quantize(Q4, rounding=ROUND_HALF_UP)


def main() -> None:
    db = SessionLocal()
    po_map = {}
    for r in db.execute(SQL):
        m = dict(r._mapping)
        p = po_map.setdefault(m["po_id"], {
            "no": m["external_order_id"], "status": m["purchase_status"],
            "paid": d(m["paid_amount"]), "items": [],
        })
        p["items"].append(m)

    changes, skips, plan = [], [], {}
    for po_id in sorted(po_map):
        info = po_map[po_id]
        if info["status"] != "pending_refine":
            continue
        allocs = list(db.execute(
            text("select id, sku_id, quantity, unit_price, amount from "
                 "purchase_allocation_items where po_id = :p"), {"p": po_id}))
        new_totals = []
        for al in allocs:
            al_id, sku_id, qty, price, amt = (d(al[0]), al[1], d(al[2]), d(al[3]), d(al[4]))
            cands = [it for it in info["items"] if it["matched_sku_id"] == sku_id]
            if not cands:
                skips.append((po_id, al_id, "无对应入库明细"))
                new_totals.append(amt)
                continue
            hit = [c for c in cands
                   if str((c["raw"] or {}).get("_1688采购订单") or "") == info["no"]]
            if hit:
                cands = hit
            if len(cands) > 1:
                same = [c for c in cands if d(c["quantity"]) == qty]
                cands = same or cands[:1]
                if not same:
                    cands = cands
            it = cands[0]
            item_qty, item_amt = d(it["quantity"]), d(it["amount_tax"])
            if item_qty <= 0 or item_amt <= 0:
                skips.append((po_id, al_id, f"明细无金额(doc{it['goodsdoc_no']}#{it['item_id']})"))
                new_totals.append(amt)
                continue
            unit = item_amt / item_qty              # 全精度
            if qty == item_qty:
                new_amount, new_unit = item_amt, unit
            else:
                new_amount, new_unit = q4(qty * unit), unit
            if price == q4(new_unit) and amt == new_amount:
                new_totals.append(amt)
                continue
            changes.append({
                "po_id": po_id, "order_no": info["no"], "alloc_id": al_id,
                "goods": str(it["goods_name"])[:18], "qty": qty,
                "old_price": price, "new_unit": new_unit,
                "old_amount": amt, "new_amount": new_amount,
                "doc": it["goodsdoc_no"], "item_id": it["item_id"],
                "note": "数量一致" if qty == item_qty else f"数量不等(明细{item_qty})",
            })
            new_totals.append(new_amount)
        if new_totals:
            plan[po_id] = (q4(sum(new_totals)), info)

    print(f"待同步分配行: {len(changes)}  跳过: {len(skips)}")
    print("\n--- 明细 ---")
    for c in changes:
        print(f"  po#{c['po_id']} {c['order_no']} alloc#{c['alloc_id']} {c['goods']:<20} "
              f"qty={c['qty']} price {c['old_price']} -> {q4(c['new_unit'])} | "
              f"amt {c['old_amount']} -> {c['new_amount']} [{c['note']} {c['doc']}]")
    for po_id, al_id, why in skips:
        print(f"  [跳过] po#{po_id} alloc#{al_id}: {why}")
    print("\n--- 同步后各单配平预测（Σ分配 vs 实付）---")
    ok = bad = 0
    for po_id, (total, info) in sorted(plan.items()):
        gap = q4(info["paid"] - total)
        flag = "✓平衡" if gap == 0 else f"差 {gap}"
        if gap == 0:
            ok += 1
        else:
            bad += 1
        print(f"  po#{po_id} {info['no']} 实付={q4(info['paid'])} 分配Σ={total} {flag}")

    if not APPLY:
        print("\n(dry-run，未写库；加 --apply 执行)")
        db.close()
        return

    for c in changes:
        db.execute(
            text("update purchase_allocation_items set quantity=:q, unit_price=:p, "
                 "amount=:a, source='inbound_auto', updated_at=now() where id=:id"),
            {"q": str(c["qty"]), "p": str(c["new_unit"]),
             "a": str(c["new_amount"]), "id": c["alloc_id"]},
        )
        audit(db, ACTOR, "purchase.allocation.sync_from_inbound",
              "purchase_allocation_items", c["alloc_id"], {
                  "poId": c["po_id"], "documentItemId": c["item_id"],
                  "goodsdocNo": c["doc"],
                  "before": {"unitPrice": str(c["old_price"]), "amount": str(c["old_amount"])},
                  "after": {"unitPrice": str(q4(c["new_unit"])), "amount": str(c["new_amount"])},
                  "basis": "按用户 Excel 修正后入库明细真实单价同步",
              })
    db.commit()
    print(f"\n已同步 {len(changes)} 条分配行；平衡 {ok} 单，仍有差额 {bad} 单。")
    db.close()


if __name__ == "__main__":
    main()
