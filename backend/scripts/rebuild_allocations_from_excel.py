# -*- coding: utf-8 -*-
"""按用户 Excel《入库申请单货品.xlsx》重建采购分配行（PO 采购内容）。

背景：一张入库单被多个 1688 订单共用时（如 RK202508040001 被 2 个订单共用、
RK202603300002-1 被 10 个订单共用），入库单反填会把整套明细灌给每个订单，
导致分配行多出不属于该订单的 SKU、金额远超实付。

规则（以 Excel「1688采购订单」列为归属依据）：
  对每个 pending_refine 且单号出现在 Excel 的采购订单：
    目标集合 = Excel 中该 1688 单号的所有行 → (申请单号, 货品编号) → (Σ数量, Σ金额)
    1) 入库单明细定位 SKU：doc=申请单号 & goods_no=货品编号；
    2) 同 SKU 已有分配行 → 改为 Excel 的数量/金额（单价=金额/数量，全精度）；
       同 SKU 多余行 → 删除；
    3) 不属于目标集合的 source='inbound_auto' 行 → 删除（错误反填）；
       人工行(source='manual') 一律保留不删；
    4) 缺 → 新建。
  验证：Σ分配 应 == 订单实付（配平即正确性校验）。
不触碰入库单 / 链路 / SKU 成本档案 / 已确认(confirmed)订单。

用法：python backend/scripts/rebuild_allocations_from_excel.py [--apply]
"""
import sys
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

import openpyxl
from sqlalchemy import text

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.audit import audit
from app.db import SessionLocal

EXCEL = next((a for a in sys.argv[1:] if a.endswith(".xlsx")), "/Users/gino/Desktop/入库申请单货品(1).xlsx")
APPLY = "--apply" in sys.argv
ACTOR = "admin"
Q4 = Decimal("0.0001")


def q4(v: Decimal) -> Decimal:
    return v.quantize(Q4, rounding=ROUND_HALF_UP)


def load_targets() -> dict:
    """1688单号 -> {(申请单号, 货品编号): [Σ数量, Σ金额]}（金额>0 才纳入）"""
    wb = openpyxl.load_workbook(EXCEL)
    rows = list(wb.worksheets[0].iter_rows(values_only=True))
    idx = {str(n): i for i, n in enumerate(rows[0])}
    out = defaultdict(lambda: defaultdict(list))
    for r in rows[1:]:
        amt = r[idx["采购总金额"]]
        amount = Decimal(str(amt)) if amt not in (None, "") else Decimal(0)
        if amount <= 0:
            continue
        order = str(r[idx["1688采购订单"]] or "").strip()
        doc_no = str(r[idx["申请单号"]] or "").strip()
        goods_no = str(r[idx["货品编号"]] or "").strip()
        qty = Decimal(str(r[idx["入库数量"]]))
        out[order][(doc_no, goods_no)].append((qty, q4(amount)))
    return {k: {kk: (sum(q for q, _ in vv), sum(a for _, a in vv)) for kk, vv in v.items()}
            for k, v in out.items()}


def main() -> None:
    targets = load_targets()
    db = SessionLocal()
    pos = list(db.execute(text(
        "select id, external_order_id, purchase_status, paid_amount "
        "from external_purchase_orders order by id")))

    plan = []
    for p in pos:
        po_id, order_no, status, paid = p[0], p[1], p[2], Decimal(str(p[3] or 0))
        if status != "pending_refine" or order_no not in targets:
            continue
        # 仅 1688 平台且为 19 位数字单号：Excel 中的临时采购内部编号（如 20260501001，
        # PDD/淘宝临时采购，无实付基准）不参与按单归属重建，避免误建分配行
        if not (order_no.isdigit() and len(order_no) == 19):
            continue
        want = targets[order_no]  # {(doc_no, goods_no): (qty, amount)}
        # 定位 SKU：申请单号+货品编号 → item.matched_sku_id
        sku_of = {}
        for (doc_no, goods_no) in want:
            row = db.execute(text(
                "select i.matched_sku_id, i.goods_name, d.goodsdoc_no from "
                "jackyun_goods_document_items i join jackyun_goods_documents d on d.id=i.document_id "
                "where d.goodsdoc_no=:dn and i.goods_no=:gn order by i.id"),
                {"dn": doc_no, "gn": goods_no}).fetchone()
            sku_of[(doc_no, goods_no)] = (row[0], row[1]) if row else (None, None)
        # 同一 SKU 可能分散在多条 Excel 记录（多张入库单 / 同一单多行）→ 按 SKU 聚合
        want_by_sku = {}
        for key, (sku, _name) in sku_of.items():
            if sku is None:
                continue
            wq, wa = want[key]
            if sku in want_by_sku:
                want_by_sku[sku] = (want_by_sku[sku][0] + wq, want_by_sku[sku][1] + wa)
            else:
                want_by_sku[sku] = (wq, wa)
        allocs = list(db.execute(text(
            "select id, sku_id, quantity, unit_price, amount, source from "
            "purchase_allocation_items where po_id=:p order by id"), {"p": po_id}))
        want_skus = set(want_by_sku)
        keep, drop, modify, create = [], [], [], []
        for al in allocs:
            al_id, sku_id, qty, price, amt, src = (
                al[0], al[1], Decimal(str(al[2])), Decimal(str(al[3]) or 0),
                Decimal(str(al[4]) or 0), al[5])
            if sku_id in want_skus:
                if sku_id in [k[0] for k in keep]:
                    if src == "inbound_auto":
                        drop.append((al_id, sku_id, "同 SKU 重复反填行"))
                    continue
                wq, wa = want_by_sku[sku_id]
                unit = wa / wq
                new_amt = q4(wq * unit)
                if q4(Decimal(str(unit))) != q4(price) or new_amt != q4(amt) or qty != wq:
                    modify.append((al_id, sku_id, wq, unit, new_amt, qty, price, amt))
                keep.append((sku_id, al_id))
            elif src == "inbound_auto":
                drop.append((al_id, sku_id, "不属于本单（多单共用入库单误反填）"))
        for sku, (wq, wa) in want_by_sku.items():
            if sku in [k[0] for k in keep]:
                continue
            create.append((sku, wq, wa / wq, wa))
        # 模拟最终状态算总额：删行=0，改行=新金额，其余=原金额，再加新建行
        mod_map = {m[0]: m[4] for m in modify}
        drop_ids = {d[0] for d in drop}
        total = q4(
            sum(mod_map.get(a[0], Decimal(str(a[4]) or 0))
                for a in allocs if a[0] not in drop_ids)
            + sum(c[3] for c in create)
        )
        plan.append({"po_id": po_id, "order_no": order_no, "paid": q4(paid),
                     "modify": modify, "drop": drop, "create": create,
                     "total": total, "sku_of": sku_of})

    n_mod = sum(len(p["modify"]) for p in plan)
    n_drop = sum(len(p["drop"]) for p in plan)
    n_new = sum(len(p["create"]) for p in plan)
    print(f"重建计划：PO {len(plan)} 个 | 改 {n_mod} 行 / 删 {n_drop} 行 / 新增 {n_new} 行")
    print("\n--- 逐单 ---")
    ok = bad = 0
    for p in plan:
        gap = q4(p["paid"] - p["total"])
        flag = "✓配平" if gap == 0 else f"差 {gap}"
        if gap == 0:
            ok += 1
        else:
            bad += 1
        print(f"po#{p['po_id']} {p['order_no']} 实付={p['paid']} 重建后Σ={p['total']} {flag}"
              f" | 改{len(p['modify'])} 删{len(p['drop'])} 增{len(p['create'])}")
        for al_id, sku, wq, unit, amt, oq, op, oa in p["modify"]:
            print(f"    ~alloc#{al_id} sku{sku}: qty {oq}->{wq} price {op}->{q4(unit)} "
                  f"amt {oa}->{amt}")
        for al_id, sku, why in p["drop"]:
            print(f"    -alloc#{al_id} sku{sku} ({why})")
        for sku, wq, unit, amt in p["create"]:
            print(f"    +new sku{sku}: qty {wq} price {q4(unit)} amt {amt}")
    print(f"\n配平 {ok} 单 / 未配平 {bad} 单")

    if not APPLY:
        print("\n(dry-run，未写库；加 --apply 执行)")
        db.close()
        return

    for p in plan:
        for al_id, sku, wq, unit, amt, oq, op, oa in p["modify"]:
            db.execute(text(
                "update purchase_allocation_items set quantity=:q, unit_price=:u, amount=:a, "
                "source='inbound_auto', updated_at=now() where id=:id"),
                {"q": str(wq), "u": str(unit), "a": str(amt), "id": al_id})
            audit(db, ACTOR, "purchase.allocation.rebuild", "purchase_allocation_items",
                  al_id, {"poId": p["po_id"],
                          "before": {"quantity": str(oq), "unitPrice": str(op), "amount": str(oa)},
                          "after": {"quantity": str(wq), "unitPrice": str(q4(unit)), "amount": str(amt)},
                          "basis": "按用户 Excel 1688采购订单归属重建"})
        for al_id, sku, why in p["drop"]:
            db.execute(text("delete from purchase_allocation_items where id=:id"), {"id": al_id})
            audit(db, ACTOR, "purchase.allocation.drop_wrong", "purchase_allocation_items",
                  al_id, {"poId": p["po_id"], "skuId": sku, "reason": why,
                          "basis": "按用户 Excel 1688采购订单归属，删除不属于本单的反填行"})
        for sku, wq, unit, amt in p["create"]:
            db.execute(text(
                "insert into purchase_allocation_items (po_id, sku_id, sku_code, goods_name, "
                "quantity, unit_price, amount, note, source, created_at, updated_at) "
                "select :po, :sku, s.sku_code, s.sku_name, :q, :u, :a, :note, 'inbound_auto', "
                "now(), now() from product_skus s where s.id=:sku"),
                {"po": p["po_id"], "sku": sku, "q": str(wq), "u": str(unit), "a": str(amt),
                 "note": "按用户 Excel 1688采购订单归属重建"})
    db.commit()
    print(f"\n已执行：改 {n_mod} / 删 {n_drop} / 增 {n_new} 行；配平 {ok} 单。")
    db.close()


if __name__ == "__main__":
    main()
