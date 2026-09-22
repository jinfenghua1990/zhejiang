# -*- coding: utf-8 -*-
"""按用户 Excel《入库申请单货品.xlsx》修正吉客云入库单明细价格（源头更正）。

口径（用户指定）：以 Excel「采购总金额」列为准（0 = 无信息，跳过）。
匹配：申请单号→goodsdoc_no；货品编号→goods_no。
规则：
  A. 同单同货品多行 Excel ↔ DB 单行且数量合计一致 → 合并修正（amount=Σ，unit=Σ/qty）。
  B. doc 228 特例：DB 单行 500@占位10，Excel 为 10 行×500（10 个 1688 订单各 500 件）
     → 拆回 10 行，每行数量/金额按 Excel，matched_sku_id 沿用原行。
  C. 数量合计不一致且非特例 → 跳过并报告，不猜。
更新：unit_price_tax/notax、amount_tax/notax（18,4），match_status→manual_adjust
（保留 manual），写 match_note，重算 doc head（Σ amount_tax）+ total_quantity。
不触碰采购分配(allocation)/SKU 成本档案/链路。

用法：python backend/scripts/fix_inbound_from_excel.py [--apply]
"""
import sys
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

import openpyxl

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.audit import audit
from app.db import SessionLocal
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.services.inbound_allocation_seed import recalc_document_amount

EXCEL = next((a for a in sys.argv[1:] if a.endswith(".xlsx")), "/Users/gino/Desktop/入库申请单货品(1).xlsx")
APPLY = "--apply" in sys.argv
ACTOR = "admin"
SPLIT_DOC_NO = "RK202603300002-1"  # 10 订单共用单，需按 Excel 拆行

Q4 = Decimal("0.0001")


def q4(v: Decimal) -> Decimal:
    return v.quantize(Q4, rounding=ROUND_HALF_UP)


def load_excel() -> dict:
    wb = openpyxl.load_workbook(EXCEL)
    rows = list(wb.worksheets[0].iter_rows(values_only=True))
    idx = {str(n): i for i, n in enumerate(rows[0])}
    groups = defaultdict(list)  # (doc_no, goods_no) -> [row]
    for r in rows[1:]:
        amt = r[idx["采购总金额"]]
        amount = Decimal(str(amt)) if amt not in (None, "") else Decimal(0)
        if amount <= 0:
            continue  # 无金额信息，不动
        doc_no = str(r[idx["申请单号"]] or "").strip()
        goods_no = str(r[idx["货品编号"]] or "").strip()
        qty = Decimal(str(r[idx["入库数量"]]))
        po1688 = str(r[idx["1688采购订单"]] or "").strip()
        groups[(doc_no, goods_no)].append(
            {"qty": qty, "amount": q4(amount), "po1688": po1688})
    return groups


def main() -> None:
    groups = load_excel()
    db = SessionLocal()
    docs = {d.goodsdoc_no: d for d in db.query(JackyunGoodsDocument).all() if d.goodsdoc_no}

    updates, splits, skips = [], [], []
    for (doc_no, goods_no), rows in sorted(groups.items()):
        doc = docs.get(doc_no)
        if doc is None:
            skips.append((doc_no, goods_no, "系统缺单据"))
            continue
        items = (
            db.query(JackyunGoodsDocumentItem)
            .filter_by(document_id=doc.id, goods_no=goods_no)
            .order_by(JackyunGoodsDocumentItem.line_no)
            .all()
        )
        if len(items) == 0:
            skips.append((doc_no, goods_no, "系统缺明细行"))
            continue
        sum_qty = sum(r["qty"] for r in rows)
        sum_amt = sum(r["amount"] for r in rows)

        # 已按 1688 订单拆行的单据：按 raw._1688采购订单 逐行核对（幂等，可反复订正）
        tagged = {(str((i.raw or {}).get("_1688采购订单") or "").strip()): i for i in items}
        if items and all(tagged.values()) and all(r["po1688"] in tagged for r in rows):
            for r in rows:
                item = tagged[r["po1688"]]
                db_qty = Decimal(str(item.quantity))
                if db_qty != r["qty"]:
                    skips.append((doc_no, goods_no,
                                  f"拆行 {r['po1688']} 数量不一致 excel={r['qty']} db={db_qty}"))
                    continue
                old_amt = q4(Decimal(str(item.amount_tax or 0)))
                new_unit = q4(r["amount"] / r["qty"])
                if old_amt == r["amount"] and q4(Decimal(str(item.unit_price_tax or 0))) == new_unit:
                    continue  # 已一致
                updates.append((doc, item, [r], old_amt, r["amount"], new_unit, r["qty"]))
            continue

        if doc_no == SPLIT_DOC_NO and len(items) == 1 and len(rows) > 1:
            splits.append((doc, items[0], rows))
            continue
        if len(items) > 1:
            skips.append((doc_no, goods_no, f"DB 同货品 {len(items)} 行，无法唯一匹配"))
            continue
        item = items[0]
        db_qty = Decimal(str(item.quantity))
        if db_qty != sum_qty:
            skips.append((doc_no, goods_no,
                          f"数量不一致 excelΣ={sum_qty} db={db_qty}"))
            continue
        old_amt = q4(Decimal(str(item.amount_tax or 0)))
        new_unit = q4(sum_amt / sum_qty)
        if old_amt == sum_amt and q4(Decimal(str(item.unit_price_tax or 0))) == new_unit:
            continue  # 已与 Excel 一致，幂等跳过
        updates.append((doc, item, rows, old_amt, sum_amt, new_unit, sum_qty))

    print(f"分组修正: {len(updates)}  拆行(doc228): {len(splits)}  跳过: {len(skips)}")
    print("\n--- A. 合并修正 ---")
    for doc, item, rows, old, new, unit, qty in updates:
        pos = ",".join(r["po1688"] for r in rows)
        print(f"  {doc.goodsdoc_no} #{item.id} {item.goods_name[:20]:<22} "
              f"qty={qty} amt {old} -> {new} (unit {unit}) 1688[{pos[:40]}]")
    print("\n--- B. 拆行 ---")
    for doc, item, rows in splits:
        print(f"  {doc.goodsdoc_no} #{item.id} {item.goods_name[:20]} → {len(rows)} 行")
        for r in rows:
            print(f"     1688 {r['po1688']} qty={r['qty']} amt={r['amount']} "
                  f"unit={q4(r['amount']/r['qty'])}")
    print("\n--- C. 跳过 ---")
    for doc_no, goods_no, why in skips:
        print(f"  [{doc_no} {goods_no}] {why}")

    if not APPLY:
        print("\n(dry-run，未写库；加 --apply 执行)")
        db.close()
        return

    touched = set()

    # A. 合并修正
    for doc, item, rows, old, new, unit, qty in updates:
        old_map = {"quantity": str(item.quantity), "unitPriceTax": str(item.unit_price_tax),
                   "amountTax": str(item.amount_tax)}
        item.unit_price_tax = unit
        item.unit_price_notax = unit
        item.amount_tax = new
        item.amount_notax = new
        if item.match_status != "manual":
            item.match_status = "manual_adjust"
        pos = ",".join(r["po1688"] for r in rows)
        item.match_note = (
            f"按用户 Excel《入库申请单货品》修正（1688:{pos[:120]}；原 {old_map}）")
        db.add(item)
        touched.add(doc.id)

    # B. doc 228 拆行
    for doc, item, rows in splits:
        old_map = {"quantity": str(item.quantity), "unitPriceTax": str(item.unit_price_tax),
                   "amountTax": str(item.amount_tax)}
        first = rows[0]
        item.quantity = first["qty"]
        item.apply_quantity = first["qty"]
        unit0 = q4(first["amount"] / first["qty"])
        item.unit_price_tax = unit0
        item.unit_price_notax = unit0
        item.amount_tax = first["amount"]
        item.amount_notax = first["amount"]
        item.match_status = "manual_adjust"
        item.match_note = (
            f"按用户 Excel 拆行修正：1688 {first['po1688']} qty={first['qty']} "
            f"amt={first['amount']}（原合并行 {old_map}）")
        item.raw = dict(item.raw or {}, _1688采购订单=first["po1688"])
        db.add(item)
        max_line = max(
            (i.line_no or 0) for i in
            db.query(JackyunGoodsDocumentItem).filter_by(document_id=doc.id).all()
        )
        for r in rows[1:]:
            unit = q4(r["amount"] / r["qty"])
            max_line += 1
            ni = JackyunGoodsDocumentItem(
                document_id=doc.id,
                line_no=max_line,  # 随后统一重排
                goods_no=item.goods_no,
                sku_barcode=item.sku_barcode,
                goods_name=item.goods_name,
                quantity=r["qty"],
                unit_name=item.unit_name,
                raw=dict(item.raw or {}, 申请数量=str(r["qty"]), 入库数量=str(r["qty"]),
                         含税单价=str(unit), 含税金额=str(r["amount"]),
                         _1688采购订单=r["po1688"]),
                spec=item.spec,
                apply_quantity=r["qty"],
                unit_price_tax=unit,
                unit_price_notax=unit,
                amount_tax=r["amount"],
                amount_notax=r["amount"],
                matched_sku_id=item.matched_sku_id,
                match_status="manual_adjust",
                match_note=f"按用户 Excel 拆行：1688 {r['po1688']} qty={r['qty']} amt={r['amount']}",
            )
            db.add(ni)
        touched.add(doc.id)

    db.commit()

    # 重排行号 + 单据 total_quantity
    for doc_id in sorted(touched):
        for seq, it in enumerate(
            db.query(JackyunGoodsDocumentItem)
            .filter_by(document_id=doc_id)
            .order_by(JackyunGoodsDocumentItem.id)
            .all(), start=1):
            if it.line_no != seq:
                it.line_no = seq
                db.add(it)
        doc_row = db.get(JackyunGoodsDocument, doc_id)
        tq = sum(Decimal(str(i.quantity or 0)) for i in
                 db.query(JackyunGoodsDocumentItem).filter_by(document_id=doc_id).all())
        print(f"  doc#{doc_id} {doc_row.goodsdoc_no}: total_quantity "
              f"{doc_row.total_quantity} -> {tq}")
        doc_row.total_quantity = tq
        db.add(doc_row)
    db.commit()

    audit(db, ACTOR, "jackyun.inbound.fix_from_excel", "jackyun_goods_document_items",
          detail={"group_updates": len(updates), "split_items": len(splits),
                  "skipped": len(skips), "documents": sorted(touched),
                  "basis": "用户 Excel 入库申请单货品.xlsx 采购总金额列"})
    db.commit()

    for doc_id in sorted(touched):
        res = recalc_document_amount(db, doc_id, actor=ACTOR)
        print(f"  head 重算 doc#{doc_id}: {res['before']} -> {res['after']}")
    print(f"\n完成：合并修正 {len(updates)} 条，拆行 {len(splits)} 组，跳过 {len(skips)} 条。")
    db.close()


if __name__ == "__main__":
    main()
