"""多因子入库预关联：1688 订单 ↔ 吉客云入库单。

因子：供应商一致 + 时间窗口 + SKU 货品重合率。金额不强参与打分（历史教训：
1688 实付含运费且存在预付/抵扣，金额口径不可靠，曾产生 1887 条噪声链）。

关键：支持 1:N 分批到货 —— 一笔 1688 订单的多个 SKU 可能分别到达多张入库单
（拆批/分批发货），所以按「剩余 SKU 覆盖」迭代建链，而不是每单只取一张：
  1. 订单的必到 SKU = 采购分配明细（purchase_allocation_items）的 sku_id 集合；
  2. 已确认关联的入库单覆盖掉的 SKU 从剩余中剔除；
  3. 候选 = 含剩余 SKU 且未被本单拒绝/确认过的入库单；
  4. 每次建链扣减新覆盖的 SKU，直到剩余为空或没有更高分候选。

产出：
- auto=True（默认）：只有有 SKU 重合且达到高置信度的候选才自动确认；没有 SKU 分配的
  订单只能生成 pending 建议，不能凭供应商、金额、时间自动串单。
- auto=False：只生成 pending 建议，供链路视图人工逐条确认。
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.audit import audit
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.models.procurement_chain import ProcurementChainLink
from app.models.purchase import ExternalPurchaseOrder, PurchaseAllocationItem
from app.services.procurement_chain_service import (
    _days_apart,
    _source_pairs,
    amount_close,
    auto_confirm_inbound_link,
    normalize_name,
    order_time,
    parse_decimal,
)

log = logging.getLogger(__name__)

AUTO_THRESHOLD = Decimal("0.90")
PENDING_THRESHOLD = Decimal("0.55")
TIME_WINDOW_DAYS = 120          # 入库单与订单日期最大可接受距离
TIME_FULL_SCORE_DAYS = 10       # ≤10 天记满分
GRACE_BEFORE_DAYS = 7           # 允许入库略早于订单（次日/当日登记）的最大天数
SIGNAL_AMOUNT_TOLERANCE = Decimal("0.05")  # 有 SKU 重合时的金额弱加分容差 5%
# 无 SKU 订单的保守辅助匹配：只建议、绝不自动确认；容差收窄到 2%、时间窗 60 天，
# 因为金额含运费/预付抵扣口径不可靠，宽一点就会像历史上一样配出噪声。
PASS2_AMOUNT_TOLERANCE = Decimal("0.02")
PASS2_WINDOW_DAYS = 60
MEDIUM_PER_ORDER = 3            # 每单中置信 pending 建议上限


def _doc_sku_map(db: Session) -> dict[int, set[int]]:
    """全部入库单 -> 其明细已匹配 SKU 的集合。"""
    rows = (
        db.query(JackyunGoodsDocumentItem.document_id, JackyunGoodsDocumentItem.matched_sku_id)
        .join(JackyunGoodsDocument, JackyunGoodsDocumentItem.document_id == JackyunGoodsDocument.id)
        .filter(
            JackyunGoodsDocument.document_type == "inbound",
            JackyunGoodsDocumentItem.matched_sku_id.isnot(None),
        )
        .all()
    )
    out: dict[int, set[int]] = {}
    for doc_id, sku_id in rows:
        out.setdefault(doc_id, set()).add(int(sku_id))
    return out


def _order_alloc_skus(db: Session, external: ExternalPurchaseOrder | None) -> set[int]:
    if external is None:
        return set()
    rows = db.query(PurchaseAllocationItem.sku_id).filter(
        PurchaseAllocationItem.po_id == external.id,
        PurchaseAllocationItem.sku_id.isnot(None),
    ).all()
    return {int(sku_id) for (sku_id,) in rows if sku_id is not None}


def _existing_links_by_order(db: Session) -> dict[int, list[ProcurementChainLink]]:
    out: dict[int, list[ProcurementChainLink]] = {}
    for link in db.query(ProcurementChainLink).filter_by(target_type="inbound").all():
        key = link.order_id if link.order_id is not None else (-link.external_po_id if link.external_po_id else None)
        if key is not None:
            out.setdefault(key, []).append(link)
    return out


def _time_score(order_at: datetime | None, doc_at: datetime | None) -> tuple[float, str]:
    """时间因子 0~1；返回 (分数, 说明)。超窗/缺时间 → (0, 说明)。"""
    if order_at is None or doc_at is None:
        return 0.0, "缺订单/入库时间"
    days = _days_apart(order_at, doc_at)
    if days is None:
        return 0.0, "时间无法比较"
    delta = doc_at.replace(tzinfo=None) - order_at.replace(tzinfo=None)
    delta_days = delta.total_seconds() / 86400
    if delta_days < -GRACE_BEFORE_DAYS:
        return 0.0, f"入库早于下单 {abs(delta_days):.0f} 天"
    if delta_days > TIME_WINDOW_DAYS:
        return 0.0, f"入库晚于下单 {delta_days:.0f} 天（超出 {TIME_WINDOW_DAYS} 天窗口）"
    score = min(1.0, TIME_FULL_SCORE_DAYS / max(days, 0.1)) if days > 0 else 1.0
    if delta_days < 0:
        score = max(score, 0.9)
    return max(0.0, score), f"时间相距 {days:.0f} 天"


def _score_candidate(
    order_supplier: str,
    doc_supplier: str,
    overlap: int,
    required_n: int,
    time_score: float,
    time_note: str,
    amount_ok: bool,
) -> tuple[Decimal, str]:
    """多因子加权打分。金额只是弱加分，绝不做主导。"""
    supplier_ok = bool(order_supplier) and normalize_name(order_supplier) == normalize_name(doc_supplier)
    overlap_ratio = (overlap / required_n) if required_n else 0.0
    score = Decimal(str(round(0.55 * overlap_ratio + 0.20 * (1 if supplier_ok else 0) + 0.25 * time_score, 4)))
    if amount_ok:
        score = min(Decimal("1.0"), score + Decimal("0.03"))
    parts = [f"SKU 重合 {overlap}/{required_n}"]
    parts.append("供应商一致" if supplier_ok else "供应商不一致/未知")
    parts.append(time_note)
    if amount_ok:
        parts.append("金额接近")
    return score, "，".join(parts)


def prelink_inbound(db: Session, actor: str = "system", auto: bool = True, commit: bool = True) -> dict:
    """执行一轮多因子入库预关联。

    auto=True：high 置信自动确认建链；auto=False：全部只生成 pending 建议（干跑预览）。
    commit=False：仅计算并 flush（拿 id），不落库（测试/预览用）。
    """
    pairs = _source_pairs(db)
    doc_skus = _doc_sku_map(db)
    # 全部入库单主档（含无 SKU 匹配的单据，供无分配订单做 pending）
    docs = {d.id: d for d in db.query(JackyunGoodsDocument).filter_by(document_type="inbound").all()}
    existing = _existing_links_by_order(db)
    target_owners: dict[int, set[tuple[str, int]]] = {}
    for link in db.query(ProcurementChainLink).filter(
        ProcurementChainLink.target_type == "inbound",
        ProcurementChainLink.match_method != "rejected",
    ).all():
        owner = (
            ("order", link.order_id)
            if link.order_id is not None
            else ("external", link.external_po_id)
        )
        if owner[1] is not None:
            target_owners.setdefault(link.target_id, set()).add(owner)

    def source_owner(order, external) -> tuple[str, int]:
        return ("order", order.id) if order is not None else ("external", external.id)

    def claimed_by_other_source(target_id: int, owner: tuple[str, int]) -> bool:
        return any(existing_owner != owner for existing_owner in target_owners.get(target_id, set()))

    auto_linked: list[dict] = []
    pending_suggested: list[dict] = []
    skipped_no_alloc_orders = 0
    fully_linked_orders = 0

    for order, external in pairs:
        order_no = (order.external_order_id if order is not None else external.external_order_id)
        workbench_id = order.id if order is not None else (-external.id if external else None)
        if workbench_id is None:
            continue
        order_supplier = (order.seller_company_name if order is not None else "") or (
            external.supplier_name if external else ""
        )
        ordered_at = order_time(order) if order is not None else (external.ordered_at if external else None)
        owner = source_owner(order, external)

        # 已确认链覆盖的 SKU
        links = existing.get(workbench_id, [])
        # 已存在（confirmed 或 pending）的 target 一律不再新插；rejected 的可重新建议
        blocked_target_ids = {l.target_id for l in links if l.match_method != "rejected"}
        confirmed_target_ids = {l.target_id for l in links if l.confirmed}
        covered_skus: set[int] = set()
        for tid in confirmed_target_ids:
            covered_skus |= doc_skus.get(tid, set())

        required = _order_alloc_skus(db, external)
        remaining = required - covered_skus if required else set()

        # 1) 有 SKU 分配的订单：按剩余 SKU 覆盖迭代（支持 1:N 拆批）
        if required:
            medium: list[tuple[Decimal, float, int, set[int], str]] = []
            for doc_id, doc in docs.items():
                if doc_id in blocked_target_ids:
                    continue
                if claimed_by_other_source(doc_id, owner):
                    continue
                overlap = doc_skus.get(doc_id, set()) & remaining
                if not overlap:
                    continue
                time_score, time_note = _time_score(ordered_at, doc.document_at)
                if time_score <= 0:
                    continue
                order_amount = parse_decimal(
                    order.actual_payment if order is not None else (external.paid_amount if external else None)
                )
                doc_amount = parse_decimal(doc.total_amount)
                amount_ok = bool(
                    order_amount is not None and doc_amount is not None
                    and amount_close(order_amount, doc_amount, SIGNAL_AMOUNT_TOLERANCE)
                )
                score, reason = _score_candidate(
                    order_supplier, doc.supplier_name or doc.company_name,
                    len(overlap), len(remaining), time_score, time_note, amount_ok,
                )
                if score < PENDING_THRESHOLD:
                    continue
                if auto and score >= AUTO_THRESHOLD:
                    # 高置信：自动建链并确认，覆盖的 SKU 从剩余中扣减（拆批继续找下一张）
                    link = ProcurementChainLink(
                        order_id=order.id if order is not None else None,
                        external_po_id=None if order is not None else external.id,
                        target_type="inbound",
                        target_id=doc_id,
                        match_method="auto",
                        confidence=score,
                        confirmed=True,
                        note=reason + "；高置信自动关联",
                    )
                    auto_confirm_inbound_link(link)
                    db.add(link)
                    db.flush()
                    blocked_target_ids.add(doc_id)
                    target_owners.setdefault(doc_id, set()).add(owner)
                    auto_linked.append({
                        "orderNo": order_no, "orderId": workbench_id,
                        "goodsdocNo": doc.goodsdoc_no, "targetId": doc_id,
                        "score": str(score), "reason": reason, "linkId": link.id,
                    })
                    remaining -= overlap
                    covered_skus |= overlap
                    if not remaining:
                        break
                else:
                    # 中置信候选仍需人工确认；自动化模式不能把建议直接变成事实链路。
                    days = _days_apart(ordered_at, doc.document_at)
                    medium.append((score, days if days is not None else 1e9, doc_id, overlap, reason))
            # 中置信候选：按分数降序、时间近者优先，最多 3 条
            for score, _, doc_id, overlap, reason in sorted(medium, key=lambda m: (-float(m[0]), m[1]))[:3]:
                if doc_id in blocked_target_ids:
                    continue
                if claimed_by_other_source(doc_id, owner):
                    continue
                doc = docs[doc_id]
                new_overlap = overlap & remaining
                if auto and not new_overlap:
                    continue
                link = ProcurementChainLink(
                    order_id=order.id if order is not None else None,
                    external_po_id=None if order is not None else external.id,
                    target_type="inbound",
                    target_id=doc_id,
                    match_method="auto",
                    confidence=score,
                    confirmed=False,
                    note=reason + "；待人工确认",
                )
                db.add(link)
                db.flush()
                blocked_target_ids.add(doc_id)
                target_owners.setdefault(doc_id, set()).add(owner)
                item = {
                    "orderNo": order_no, "orderId": workbench_id,
                    "goodsdocNo": doc.goodsdoc_no, "targetId": doc_id,
                    "score": str(score), "reason": reason, "linkId": link.id,
                }
                pending_suggested.append(item)
            if not remaining:
                fully_linked_orders += 1
            continue

        # 2) 无 SKU 分配（还没细化采购内容）：按「供应商+金额+时间」只建立待确认候选。
        #    这些信号不足以证明入库单属于当前订单，禁止自动确认。
        skipped_no_alloc_orders += 1
        if order is None and external is None:
            continue
        if confirmed_target_ids:
            continue  # 已有确认入库关联：需要更多入库时由 SKU 分配后 pass-1 或人工补充
        order_amount = parse_decimal(
            order.actual_payment if order is not None else (external.paid_amount if external else None)
        )
        if not order_amount or not order_supplier or ordered_at is None:
            continue
        best: list[tuple[float, JackyunGoodsDocument]] = []
        for doc_id, doc in docs.items():
            if doc_id in blocked_target_ids or doc_id in confirmed_target_ids:
                continue
            if claimed_by_other_source(doc_id, owner):
                continue
            if normalize_name(order_supplier) != normalize_name(doc.supplier_name or doc.company_name or ""):
                continue
            if not amount_close(order_amount, parse_decimal(doc.total_amount), PASS2_AMOUNT_TOLERANCE):
                continue
            days = _days_apart(ordered_at, doc.document_at)
            if days is None or days > PASS2_WINDOW_DAYS:
                continue
            best.append((days, doc))
        if not best:
            continue
        best.sort(key=lambda item: (item[0], item[1].id))
        days, doc = best[0]
        score = Decimal("0.5").quantize(Decimal("0.0001"))
        reason = f"供应商一致、金额接近（容差 {PASS2_AMOUNT_TOLERANCE*100:.0f}%）、时间相距 {days:.0f} 天；订单尚未分配 SKU，请人工核对后确认"
        link = ProcurementChainLink(
            order_id=order.id if order is not None else None,
            external_po_id=None if order is not None else external.id,
            target_type="inbound", target_id=doc.id,
            match_method="auto", confidence=score, confirmed=False,
            note=reason + "；待人工确认",
        )
        db.add(link)
        db.flush()
        blocked_target_ids.add(doc.id)
        target_owners.setdefault(doc.id, set()).add(owner)
        pending_suggested.append({
            "orderNo": order_no, "orderId": workbench_id,
            "goodsdocNo": doc.goodsdoc_no, "targetId": doc.id,
            "score": str(score), "reason": reason, "linkId": link.id,
        })

    auto_confirm_result: dict = {}
    if commit:
        db.commit()
        # 与 UI 动线一致：auto 确认的入库链 → 自动反填 SKU 分配（幂等，人工填优先）
        if auto_linked:
            from app.services.inbound_allocation_seed import seed_for_link_batch
            try:
                link_objs = [db.get(ProcurementChainLink, item["linkId"]) for item in auto_linked]
                seed_for_link_batch(db, [l for l in link_objs if l is not None])
            except Exception as exc:
                log.warning("prelink 反填 seed_for_link_batch 失败：%s", exc)
        if auto:
            from app.services.procurement_chain_service import auto_confirm_pending_links
            auto_confirm_result = auto_confirm_pending_links(db, actor=actor)
        audit(db, actor, "purchase.procurement_chain.prelink", "procurement_chain_links", 0,
              {"autoLinked": len(auto_linked), "pendingSuggested": len(pending_suggested),
               "fullyLinkedOrders": fully_linked_orders, "noAllocOrders": skipped_no_alloc_orders,
               "auto": auto, "autoConfirmed": auto_confirm_result})
    else:
        db.rollback()
    return {
        "autoLinked": auto_linked,
        "pendingSuggested": pending_suggested,
        "stats": {
            "autoLinked": len(auto_linked),
            "pendingSuggested": len(pending_suggested),
            "fullyLinkedOrders": fully_linked_orders,
            "noAllocOrders": skipped_no_alloc_orders,
        },
        "autoConfirmed": auto_confirm_result if commit and auto else {},
    }
