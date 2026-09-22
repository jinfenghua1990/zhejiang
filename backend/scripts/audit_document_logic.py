# -*- coding: utf-8 -*-
"""单据逻辑关系全面体检 v2（只读，不改库）。

v2 修正口径（避免误报）：
  - 1688 discount 字段是带符号调整额（负数=优惠），勾稽公式 = goods+freight+discount。
  - 副本 order_amount 含运费，与 1688 goods_total(纯商品) 的口径差 = 运费，不算错误；
    只把 paid_amount 与 1688 actual_payment 不一致判为真异常。
  - 入库单零金额明细：Σ已有金额 == head 时是赠品行（不报）；< head 才是占位缺口。
  - 入库链路金额按「订单聚合 Σ入库」核对，多单共享（拆分/合并）单独列出不判金额错。
"""
from __future__ import annotations

from decimal import Decimal
from collections import defaultdict

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.models.alibaba1688_import import Alibaba1688Order, Alibaba1688FileImport
from app.models.purchase import ExternalPurchaseOrder, PurchaseAllocationItem
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem, JackyunPurchaseSettlement
from app.models.tax import TaxInvoice, TaxInvoiceLink
from app.models.procurement_chain import ProcurementChainLink

EPS = Decimal("0.01")


def d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal("0")


def fmt(v) -> str:
    return f"{d(v):,.2f}"


def close(a, b, eps=EPS) -> bool:
    return abs(d(a) - d(b)) <= eps


def main():
    db = SessionLocal()
    # 结构：{模块: [(级别, 描述)]}
    F: dict[str, list[tuple[str, str]]] = defaultdict(list)

    def add(mod, lvl, msg):
        F[mod].append((lvl, msg))

    # ---- 可见订单口径：active import + active row ----
    active_import_ids = {i.id for i in db.query(Alibaba1688FileImport).filter(
        Alibaba1688FileImport.lifecycle == "active").all()}
    orders = db.query(Alibaba1688Order).all()
    visible = [o for o in orders if o.row_status == "active" and o.import_id in active_import_ids]

    # ============ A. 1688 订单 ============
    M = "A. 1688 订单"
    for o in orders:
        vis = "可见" if (o.row_status == "active" and o.import_id in active_import_ids) else "隐藏"
        # 勾稽：goods + freight + discount vs 实付（discount 带符号）
        calc = d(o.goods_total) + d(o.freight) + d(o.discount)
        if d(o.actual_payment) > 0 and not close(calc, d(o.actual_payment), Decimal("0.05")):
            add(M, "P3", f"#{o.id} {o.external_order_id}[{vis}] 勾稽差：商品{d(o.goods_total)}+运费{d(o.freight)}+调整{d(o.discount)}={fmt(calc)} ≠ 实付{d(o.actual_payment)}（差 {fmt(d(o.actual_payment)-calc)}，多为红包/满减未入字段）")
        # 负实付
        if d(o.actual_payment) < 0:
            add(M, "P1", f"#{o.id} {o.external_order_id}[{vis}] 实付为负 {d(o.actual_payment)}")
        # 有实付无付款时间
        if d(o.actual_payment) > 0 and o.pay_time is None:
            add(M, "P2", f"#{o.id} {o.external_order_id}[{vis}] 有实付 {d(o.actual_payment)} 但无付款时间（{o.order_status}）")
        # 交易关闭/退款但实付>0（重点：可见的）
        if o.order_status in ("交易关闭", "已关闭", "退款成功", "退款中") and d(o.actual_payment) > 0:
            lvl = "P1" if vis else "P3"
            add(M, lvl, f"#{o.id} {o.external_order_id}[{vis}] 状态「{o.order_status}」但实付 {d(o.actual_payment)}>0（疑似退款未归零）")
        # 时间倒挂
        if o.pay_time and o.order_time and o.pay_time < o.order_time:
            add(M, "P3", f"#{o.id} 付款时间({o.pay_time.date()})早于下单时间({o.order_time.date()})")

    # ============ B. 工作流副本 ============
    M = "B. 工作流副本"
    pos = db.query(ExternalPurchaseOrder).all()
    alibaba_by_ext = {o.external_order_id: o for o in orders}
    po_by_ext = {p.external_order_id: p for p in pos}
    for p in pos:
        o = alibaba_by_ext.get(p.external_order_id)
        if p.platform == "1688" and o is None:
            add(M, "P2", f"po#{p.id} {p.external_order_id} 标 1688 但无 alibaba1688_orders 行（孤儿副本）")
            continue
        if o is None:
            continue
        vis = "可见" if (o.row_status == "active" and o.import_id in active_import_ids) else "隐藏"
        # 实付一致性（关键）
        if not close(o.actual_payment, p.paid_amount):
            add(M, "P1", f"po#{p.id} {p.external_order_id}[{vis}] 副本实付{d(p.paid_amount)} ≠ 1688 实付{d(o.actual_payment)}")
        # order_amount vs goods_total：差异是否=运费（含运费口径）
        diff = d(p.order_amount) - d(o.goods_total)
        if not close(diff, d(o.freight), Decimal("0.05")) and d(p.order_amount) > 0:
            add(M, "P3", f"po#{p.id} {p.external_order_id}[{vis}] 副本订单金额{d(p.order_amount)} vs 1688 商品{d(o.goods_total)}+运费{d(o.freight)}（差 {fmt(diff - d(o.freight))}）")
    # 1688 无副本
    for ext, o in alibaba_by_ext.items():
        if ext not in po_by_ext:
            add(M, "P3", f"1688 #{o.id} {ext} 无工作流副本")

    # ============ C. 入库单 ============
    M = "C. 入库单"
    docs = db.query(JackyunGoodsDocument).all()
    items = db.query(JackyunGoodsDocumentItem).all()
    items_by_doc = defaultdict(list)
    for it in items:
        items_by_doc[it.document_id].append(it)
    for doc in [x for x in docs if x.document_type == "inbound"]:
        its = items_by_doc.get(doc.id, [])
        sum_amt = sum(d(it.amount_tax) for it in its)
        sum_qty = sum(d(it.quantity) for it in its)
        if d(doc.total_amount) > 0 and not close(d(doc.total_amount), sum_amt):
            add(M, "P1", f"doc#{doc.id} {doc.goodsdoc_no} head金额{fmt(doc.total_amount)} ≠ Σ明细{fmt(sum_amt)}（差 {fmt(d(doc.total_amount)-sum_amt)}）")
        if d(doc.total_quantity) > 0 and not close(d(doc.total_quantity), sum_qty, Decimal("0.001")):
            add(M, "P2", f"doc#{doc.id} {doc.goodsdoc_no} head数量{doc.total_quantity} ≠ Σ明细{sum_qty}")
        # 单价×数量 vs 金额（真异常）
        for it in its:
            if d(it.quantity) > 0 and d(it.unit_price_tax) > 0 and d(it.amount_tax) > 0:
                expect = d(it.unit_price_tax) * d(it.quantity)
                if not close(expect, d(it.amount_tax)):
                    add(M, "P1", f"doc#{doc.id} 明细#{it.id} {it.goods_name} 单价{d(it.unit_price_tax)}×{d(it.quantity)}={fmt(expect)} ≠ 金额{d(it.amount_tax)}")
        # 零金额明细：Σ已有==head 是赠品（不报）；Σ已有<head 且 Σ已有+零金额==head 才是占位缺口
        zero_amt = [it for it in its if d(it.amount_tax) == 0 and d(it.quantity) > 0 and d(it.unit_price_tax) > 0]
        if zero_amt:
            zero_sum = sum(d(it.unit_price_tax) * d(it.quantity) for it in zero_amt)
            if sum_amt < d(doc.total_amount) - Decimal("0.01") and close(sum_amt + zero_sum, d(doc.total_amount)):
                add(M, "P2", f"doc#{doc.id} {doc.goodsdoc_no} 有 {len(zero_amt)} 行零金额(单价×数量={fmt(zero_sum)})，head {fmt(doc.total_amount)} 缺 {fmt(d(doc.total_amount)-sum_amt)}，可回填")

    # ============ D. 入库链路 ============
    M = "D. 入库链路"
    inbound_links = db.query(ProcurementChainLink).filter(ProcurementChainLink.target_type == "inbound").all()
    doc_to_keys = defaultdict(set)
    for l in inbound_links:
        key = l.order_id if l.order_id is not None else -l.external_po_id
        doc_to_keys[l.target_id].add(key)
    for doc_id, keys in doc_to_keys.items():
        if len(keys) > 1:
            doc = db.query(JackyunGoodsDocument).get(doc_id)
            add(M, "P3", f"doc#{doc_id} {doc.goodsdoc_no if doc else '?'} 被 {len(keys)} 个订单关联 {sorted(keys)}（拆分/合并，需核对分摊）")
    # 订单聚合 Σ入库金额 vs 实付（共享单跳过金额核对，因分摊金额未记录）
    shared_docs = {doc_id for doc_id, keys in doc_to_keys.items() if len(keys) > 1}
    order_inbound_sum = defaultdict(lambda: Decimal("0"))
    order_paid = {}
    for l in inbound_links:
        if l.target_id in shared_docs:
            continue
        if l.order_id is not None:
            o = db.query(Alibaba1688Order).get(l.order_id)
            paid = d(o.actual_payment) if o else Decimal("0")
            key = f"order#{l.order_id}"
        else:
            p = db.query(ExternalPurchaseOrder).get(l.external_po_id)
            paid = d(p.paid_amount) if p else Decimal("0")
            key = f"po#{l.external_po_id}"
        doc = db.query(JackyunGoodsDocument).get(l.target_id)
        order_inbound_sum[key] += d(doc.total_amount) if doc else Decimal("0")
        order_paid[key] = paid
    for key, s in order_inbound_sum.items():
        paid = order_paid.get(key, Decimal("0"))
        if paid > 0 and not close(s, paid, paid * Decimal("0.02")):
            add(M, "P2", f"{key} Σ入库金额 {fmt(s)} vs 订单实付 {fmt(paid)}（差 {fmt(s-paid)}，可能部分入库/分批未结）")

    # ============ E. 发票 ============
    M = "E. 发票"
    invoices = db.query(TaxInvoice).all()
    input_inv = [i for i in invoices if i.direction == "input"]
    red_status = [i for i in invoices if i.status == "red"]
    for i in red_status:
        if d(i.total_amount) > 0:
            add(M, "P3", f"发票#{i.id} {i.invoice_number} status=red 但正数 {d(i.total_amount)}（已红冲作废，不应计票）")
    links = db.query(TaxInvoiceLink).all()
    target_inv = defaultdict(list)
    for l in links:
        target_inv[(l.target_type, l.target_id)].append(l)
    for (ttype, tid), ls in target_inv.items():
        inv_sum = sum(d(db.query(TaxInvoice).get(l.invoice_id).total_amount) for l in ls)
        if ttype == "alibaba1688_order":
            o = db.query(Alibaba1688Order).get(tid)
            paid = d(o.actual_payment) if o else Decimal("0")
        else:
            p = db.query(ExternalPurchaseOrder).get(tid)
            paid = d(p.paid_amount) if p else Decimal("0")
        if paid > 0 and inv_sum > paid + EPS:
            add(M, "P1", f"{ttype}#{tid} 关联票面 {fmt(inv_sum)} > 订单实付 {fmt(paid)}（多 {fmt(inv_sum-paid)}，换开/重开重复）")
        if len(ls) > 1:
            invs = [db.query(TaxInvoice).get(l.invoice_id) for l in ls]
            desc = "、".join(f"{i.invoice_number}({i.issue_date.date() if i.issue_date else '?'},{i.status})" for i in invs)
            add(M, "P2", f"{ttype}#{tid} 关联 {len(ls)} 张票：{desc}")

    # ============ F. 付款分配 ============
    M = "F. 付款分配"
    st = db.query(JackyunPurchaseSettlement).count()
    allocs = db.query(PurchaseAllocationItem).all()
    po_alloc = defaultdict(lambda: Decimal("0"))
    for a in allocs:
        po_alloc[a.po_id] += d(a.amount)
        if d(a.quantity) is not None and d(a.unit_price) is not None and d(a.amount) is not None:
            expect = d(a.quantity) * d(a.unit_price)
            if not close(expect, d(a.amount)):
                add(M, "P1", f"分配行#{a.id} po#{a.po_id} {d(a.quantity)}×{d(a.unit_price)}={fmt(expect)} ≠ 金额{d(a.amount)}")
    for po_id, amt in po_alloc.items():
        p = db.query(ExternalPurchaseOrder).get(po_id)
        if not p:
            add(M, "P1", f"分配行指向不存在的 po#{po_id}")
            continue
        paid = p.effective_paid_amount
        if paid and paid > 0 and not close(amt, paid):
            add(M, "P2", f"po#{po_id} {p.external_order_id} Σ分配{fmt(amt)} ≠ 有效实付{fmt(paid)}（差 {fmt(amt-paid)}）")

    db.close()

    # ============ 输出 ============
    print("=" * 72)
    print("单据逻辑关系体检报告 v2")
    print("=" * 72)
    print(f"可见订单 {len(visible)}/{len(orders)}（active import + active row）")
    print(f"工作流副本 {len(pos)} 条；入库单 {len([x for x in docs if x.document_type=='inbound'])} 张；"
          f"进项发票 {len(input_inv)} 张；发票关联 {len(links)} 条；分配行 {len(allocs)} 条；结算单 {st} 条\n")

    order_lvl = {"P1": 0, "P2": 1, "P3": 2}
    total = {"P1": 0, "P2": 0, "P3": 0}
    for mod in ["A. 1688 订单", "B. 工作流副本", "C. 入库单", "D. 入库链路", "E. 发票", "F. 付款分配"]:
        rows = sorted(F.get(mod, []), key=lambda x: order_lvl[x[0]])
        if not rows:
            continue
        print(f"\n### {mod}（{len(rows)} 条）")
        for lvl, msg in rows:
            total[lvl] += 1
            print(f"  [{lvl}] {msg}")
    print("\n" + "=" * 72)
    print(f"分级统计：P1(真错误) {total['P1']} / P2(需关注) {total['P2']} / P3(提示/口径) {total['P3']}")


if __name__ == "__main__":
    main()
