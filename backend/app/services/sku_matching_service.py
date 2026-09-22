"""SKU 匹配服务：入库明细自动匹配 + 1688 订单 SKU 人工配置辅助。

规则（2026-09-04 与用户对齐）：
- 入库明细 ↔ 货品档案：goodsNo 直接命中 → auto；入库金额是实际成本事实，
  不再与货品档案的历史 default_cost 比较并阻塞入库链路。
  异常打标保留给 missing（档案无此货号）和人工明确标记的成本核验。
- 1688 订单 ↔ SKU：导入源数据无商品标题，天然人工配置。本服务只提供候选排序
  （同供应商历史入库货品优先）与金额平衡校验，配错提示异常。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.catalog import Product, ProductSku
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.models.purchase import ExternalPurchaseOrder, PurchaseAllocationItem
from app.core.audit import audit
from app.services.procurement_chain_service import normalize_name

AMOUNT_TOLERANCE = Decimal("0.02")
ABS_EPS = Decimal("0.5")
INBOUND_AMOUNT_KEYS = ("采购总金额", "价税合计", "含税金额", "入库金额", "金额")


# ---------- 入库明细：自动匹配 ----------

def _catalog_lookup(db: Session) -> dict[str, tuple[Product | None, ProductSku | None]]:
    """吉客云货品号 / SKU 编码 / 条码 → (货品, SKU)。

    优先登记 SKU 级别键，避免未来出现“一货品多 SKU”时随意取第一条。
    """
    products = {product.id: product for product in db.query(Product).all()}
    lookup: dict[str, tuple[Product | None, ProductSku | None]] = {}
    skus_by_pid: dict[int, list[ProductSku]] = {}
    for sku in db.query(ProductSku).all():
        if sku.product_id is not None:
            skus_by_pid.setdefault(sku.product_id, []).append(sku)
        entry = (products.get(sku.product_id), sku)
        for key in (sku.jackyun_sku_id, sku.sku_code, sku.barcode):
            if key:
                lookup[str(key).strip()] = entry
    for product in products.values():
        product_skus = skus_by_pid.get(product.id, [])
        # 只有唯一 SKU 时，货品号才能安全直达 SKU；多 SKU 时留给人工选择。
        sku = product_skus[0] if len(product_skus) == 1 else None
        entry = (product, sku)
        for key in (product.jackyun_goods_id, product.goods_code):
            if key and str(key).strip() not in lookup:
                lookup[str(key).strip()] = entry
    return lookup


def match_inbound_items(db: Session, include_outbound: bool = False) -> dict[str, Any]:
    """goodsNo / SKU 条码自动匹配 + 金额校验；人工指定结果永不覆盖。"""
    lookup = _catalog_lookup(db)
    q = (
        db.query(JackyunGoodsDocumentItem, JackyunGoodsDocument.document_type)
        .join(JackyunGoodsDocument, JackyunGoodsDocumentItem.document_id == JackyunGoodsDocument.id)
    )
    if not include_outbound:
        q = q.filter(JackyunGoodsDocument.document_type == "inbound")
    stats = {"total": 0, "price_ok": 0, "auto": 0, "manual": 0, "missing": 0, "price_mismatch": 0}
    for item, _doc_type in q.all():
        stats["total"] += 1
        # 人工指定或人工解除都属于人工锁定；不能在下一轮扫描中悄悄重新绑定。
        if item.match_status == "manual":
            stats["manual"] += 1
            continue
        goods_no = (item.goods_no or (item.raw or {}).get("goodsNo") or (item.raw or {}).get("goodsCode") or "").strip()
        sku_key = (item.sku_barcode or (item.raw or {}).get("skuBarcode") or "").strip()
        entry = lookup.get(sku_key) or lookup.get(goods_no)
        if entry is None:
            item.matched_sku_id = None
            item.match_status = "missing"
            item.match_note = f"货品档案无此货号 {goods_no or '(空)'}，需补档案或人工指定"
            stats["missing"] += 1
            continue
        product, sku = entry
        item.matched_sku_id = sku.id if sku else None
        if sku is None:
            item.match_status = "auto"
            item.match_note = f"命中货品「{product.goods_name if product else goods_no}」但无法唯一确定 SKU，需人工指定"
            stats["auto"] += 1
            continue
        # 入库单金额/数量是本次采购成本事实；default_cost 只是没有入库数据时的兜底。
        amount = item.amount_tax
        qty = item.quantity
        raw = item.raw or {}
        has_source_amount = any(
            raw.get(key) is not None and str(raw.get(key)).strip()
            for key in INBOUND_AMOUNT_KEYS
        )
        if amount is not None and qty is not None and amount >= 0 and qty > 0 and (has_source_amount or amount > 0):
            item.match_status = "price_ok"
            item.match_note = "采购入库金额是实际成本事实；货品档案成本仅作无入库数据时的兜底"
            stats["price_ok"] += 1
        else:
            item.match_status = "auto"
            item.match_note = "" if amount is not None and has_source_amount else "金额信息不全，未做金额校验"
            stats["auto"] += 1
    db.commit()
    return stats


def inbound_match_summary(db: Session) -> dict[str, Any]:
    """入库明细匹配状态汇总 + 异常清单。"""
    rows = (
        db.query(
            JackyunGoodsDocumentItem.match_status,
            JackyunGoodsDocument.id,
            JackyunGoodsDocument.goodsdoc_no,
            JackyunGoodsDocumentItem,
        )
        .join(JackyunGoodsDocument, JackyunGoodsDocumentItem.document_id == JackyunGoodsDocument.id)
        .filter(JackyunGoodsDocument.document_type == "inbound")
        .all()
    )
    counts: dict[str, int] = {"price_ok": 0, "auto": 0, "missing": 0, "price_mismatch": 0, "unmatched": 0}
    anomalies: list[dict[str, Any]] = []
    manual_matches: list[dict[str, Any]] = []
    for status, doc_id, doc_no, item in rows:
        key = status or "unmatched"
        counts[key] = counts.get(key, 0) + 1
        if key in ("missing", "price_mismatch", "manual"):
            serialized = {
                "itemId": item.id,
                "documentId": doc_id,
                "goodsdocNo": doc_no,
                "goodsNo": item.goods_no,
                "goodsName": item.goods_name,
                "quantity": float(item.quantity) if item.quantity is not None else None,
                "amountTax": float(item.amount_tax) if item.amount_tax is not None else None,
                "matchedSkuId": item.matched_sku_id,
                "status": key,
                "note": item.match_note,
            }
            if key == "manual":
                manual_matches.append(serialized)
            else:
                anomalies.append(serialized)
    total = sum(counts.values())
    return {
        "total": total,
        "counts": counts,
        "anomalies": anomalies[:200],
        "manualMatches": manual_matches[:200],
    }


# ---------- 1688 订单：SKU 配置辅助 ----------

def _supplier_goods_history(db: Session) -> dict[str, dict[int, int]]:
    """归一化供应商名 → {sku_id: 出现次数}，来源：历史入库单明细的自动匹配结果。"""
    rows = (
        db.query(JackyunGoodsDocument.supplier_name, JackyunGoodsDocumentItem.matched_sku_id)
        .join(JackyunGoodsDocumentItem, JackyunGoodsDocumentItem.document_id == JackyunGoodsDocument.id)
        .filter(JackyunGoodsDocument.document_type == "inbound")
        .all()
    )
    history: dict[str, dict[int, int]] = {}
    for supplier, sku_id in rows:
        if not sku_id:
            continue
        key = normalize_name(supplier)
        if not key:
            continue
        history.setdefault(key, {})
        history[key][sku_id] = history[key].get(sku_id, 0) + 1
    return history


def allocation_candidates(db: Session, po: ExternalPurchaseOrder, limit: int = 20) -> list[dict[str, Any]]:
    """为 1688 订单推荐候选 SKU：同供应商历史入库货品优先（按出现次数），其余补活跃 SKU。"""
    history = _supplier_goods_history(db)
    supplier_key = normalize_name(po.supplier_name)
    ranked: list[tuple[int, int]] = sorted(history.get(supplier_key, {}).items(), key=lambda kv: -kv[1])
    top_ids = [sku_id for sku_id, _n in ranked[:limit]]
    out: list[dict[str, Any]] = []
    if top_ids:
        skus = db.query(ProductSku).filter(ProductSku.id.in_(top_ids)).all()
        by_id = {s.id: s for s in skus}
        for sku_id in top_ids:
            s = by_id.get(sku_id)
            if not s:
                continue
            out.append({
                "skuId": s.id, "skuCode": s.sku_code, "skuName": s.sku_name,
                "barcode": s.barcode, "unit": s.unit,
                "defaultCost": float(s.default_cost) if s.default_cost is not None else None,
                "reason": f"该供应商历史入库 {history[supplier_key][sku_id]} 次",
            })
    used_ids = {row["skuId"] for row in out}
    if len(out) < limit:
        fallback = (
            db.query(ProductSku)
            .filter(ProductSku.status == "active")
            .order_by(ProductSku.sku_code, ProductSku.id)
            .limit(limit * 3)
            .all()
        )
        if not fallback:
            fallback = db.query(ProductSku).order_by(ProductSku.sku_code, ProductSku.id).limit(limit * 3).all()
        for sku in fallback:
            if sku.id in used_ids:
                continue
            out.append({
                "skuId": sku.id,
                "skuCode": sku.sku_code,
                "skuName": sku.sku_name,
                "barcode": sku.barcode,
                "unit": sku.unit,
                "defaultCost": float(sku.default_cost) if sku.default_cost is not None else None,
                "reason": "吉客云 SKU 主档候选",
            })
            used_ids.add(sku.id)
            if len(out) >= limit:
                break
    return out[:limit]


def allocation_balance(db: Session, po: ExternalPurchaseOrder) -> dict[str, Any]:
    """分配金额合计 vs 实付金额；偏差超 2% 或 0.5 元 → abnormal。"""
    total = db.query(PurchaseAllocationItem).filter_by(po_id=po.id).all()
    allocated = sum((r.amount or Decimal("0")) for r in total)
    paid = po.paid_amount or Decimal("0")
    diff = allocated - paid
    balanced = abs(diff) <= max(paid * AMOUNT_TOLERANCE, ABS_EPS)
    return {
        "allocated": float(allocated), "paid": float(paid), "diff": float(diff),
        "balanced": balanced,
        "abnormalNote": "" if balanced else f"分配合计 {allocated:.2f} 与实付 {paid:.2f} 差 {diff:.2f}，请核对",
    }


# ---------- 成本确认（1688 采购单锚定，2026-09-04 用户确认口径） ----------

def _linked_order_ratio(db: Session, document_id: int) -> tuple[Decimal, Decimal] | None:
    """入库单若已挂采购主单，返回 (采购实付, 入库单金额) 供比例分摊。"""
    from app.models.procurement_chain import ProcurementChainLink
    from app.models.alibaba1688_import import Alibaba1688Order
    from app.models.purchase import ExternalPurchaseOrder

    link = (
        db.query(ProcurementChainLink)
        .filter(ProcurementChainLink.target_type == "inbound", ProcurementChainLink.target_id == document_id)
        .first()
    )
    if not link:
        return None
    paid = None
    if link.order_id is not None:
        order = db.get(Alibaba1688Order, link.order_id)
        paid = order.actual_payment if order is not None else None
    elif link.external_po_id is not None:
        external = db.get(ExternalPurchaseOrder, link.external_po_id)
        paid = external.effective_paid_amount if external is not None else None
    else:
        return None
    doc = db.get(JackyunGoodsDocument, document_id)
    if paid is None or not doc or not doc.total_amount or doc.total_amount <= 0:
        return None
    return Decimal(str(paid)), Decimal(str(doc.total_amount))


def _confirm_candidate_cost(db: Session, item: JackyunGoodsDocumentItem) -> dict[str, Any] | None:
    """计算一条异常明细的确认成本与依据；无法计算返回 None。"""
    if item.amount_tax is None or item.quantity is None or item.quantity <= 0:
        return None
    anchor = _linked_order_ratio(db, item.document_id)
    if anchor:
        order_paid, doc_total = anchor
        ratio = order_paid / doc_total
        adjusted = Decimal(str(item.amount_tax)) * ratio
        unit = adjusted / Decimal(str(item.quantity))
        return {
            "basis": "1688_order",
            "unit_cost": unit.quantize(Decimal("0.0001")),
            "note": (
                f"已确认成本（1688锚定）：实付 ¥{order_paid} / 单据 ¥{doc_total} 比例分摊 "
                f"→ {unit.quantize(Decimal('0.0001'))}"
            ),
        }
    unit = Decimal(str(item.amount_tax)) / Decimal(str(item.quantity))
    return {
        "basis": "inbound_direct",
        "unit_cost": unit.quantize(Decimal("0.0001")),
        "note": f"已确认成本（入库单价，未挂1688订单）：¥{item.amount_tax} / {item.quantity:g} → {unit.quantize(Decimal('0.0001'))}",
    }



def _sku_unit_price_median(db: Session, sku_id: int) -> Decimal | None:
    """该 SKU 全部入库历史单价中位数（不限匹配状态），守门基准。"""
    from statistics import median

    rows = (
        db.query(JackyunGoodsDocumentItem.amount_tax, JackyunGoodsDocumentItem.quantity)
        .join(JackyunGoodsDocument, JackyunGoodsDocumentItem.document_id == JackyunGoodsDocument.id)
        .filter(
            JackyunGoodsDocumentItem.matched_sku_id == sku_id,
            JackyunGoodsDocument.document_type == "inbound",
            JackyunGoodsDocumentItem.amount_tax.isnot(None),
            JackyunGoodsDocumentItem.quantity.isnot(None),
        )
        .all()
    )
    prices = [
        Decimal(str(a)) / Decimal(str(q))
        for a, q in rows
        if q and Decimal(str(q)) > 0 and a and Decimal(str(a)) > 0
    ]
    return median(prices) if prices else None


def confirm_anomaly_costs(db: Session, document_id: int | None = None, only_item_id: int | None = None) -> dict[str, Any]:
    """确认成本异常项：1688 挂接优先比例分摊，否则按入库单价；偏离同 SKU 候选中位数 >50% 的跳过待人工核单。"""
    from statistics import median

    q = db.query(JackyunGoodsDocumentItem).filter(
        JackyunGoodsDocumentItem.match_status == "price_mismatch",
        JackyunGoodsDocumentItem.matched_sku_id.isnot(None),
    )
    if document_id is not None:
        q = q.filter(JackyunGoodsDocumentItem.document_id == document_id)
    if only_item_id is not None:
        q = q.filter(JackyunGoodsDocumentItem.id == only_item_id)
    items = q.all()

    # 预算候选单价，做同 SKU 中位数守门（防单据录错毒化档案成本）
    candidates: dict[int, list[tuple[JackyunGoodsDocumentItem, dict[str, Any]]]] = {}
    median_pool = items if only_item_id is None else (
        db.query(JackyunGoodsDocumentItem).filter(
            JackyunGoodsDocumentItem.match_status == "price_mismatch",
            JackyunGoodsDocumentItem.matched_sku_id.isnot(None),
        ).all()
    )
    for item in median_pool:
        cand = _confirm_candidate_cost(db, item)
        if cand is None:
            continue
        candidates.setdefault(item.matched_sku_id, []).append((item, cand))
    if only_item_id is not None:
        items = [i for i in items]
        candidates = {sku_id: [(i, c) for i, c in pairs if i.id == only_item_id]
                      for sku_id, pairs in candidates.items()
                      if any(i.id == only_item_id for i, _ in pairs)}

    medians: dict[int, Decimal] = {}
    for sku_id, pairs in candidates.items():
        # 守门基准 = 该 SKU 全部入库历史单价中位数（不限状态，防池子退化）
        medians[sku_id] = _sku_unit_price_median(db, sku_id) or median(p[1]["unit_cost"] for p in pairs)

    confirmed, skipped, confirmed_rows = 0, [], []
    for sku_id, pairs in candidates.items():
        med = medians[sku_id]
        for item, cand in pairs:
            unit = cand["unit_cost"]
            if med > 0 and abs(unit - med) > med * Decimal("0.5"):
                item.match_note = (
                    f"⚠️ 单价 {unit} 偏离同 SKU 各单据中位数 {med} 超 50%，疑似单据金额/数量录错，"
                    f"请核对吉客云单据后再人工确认"
                )
                skipped.append({
                    "itemId": item.id, "skuId": sku_id,
                    "unitCost": str(unit), "median": str(med),
                })
                continue
            sku = db.get(ProductSku, sku_id)
            if not sku:
                skipped.append({"itemId": item.id, "skuId": sku_id, "reason": "SKU 不存在"})
                continue
            old_cost = sku.default_cost
            sku.default_cost = unit
            item.match_status = "manual"
            item.match_note = cand["note"] + (
                f"（档案成本 {old_cost} → {unit}）" if old_cost != unit else ""
            )
            confirmed += 1
            confirmed_rows.append({
                "itemId": item.id, "skuId": sku_id,
                "oldCost": str(old_cost) if old_cost is not None else None,
                "newCost": str(unit), "basis": cand["basis"],
            })
    db.commit()
    return {"confirmed": confirmed, "confirmedRows": confirmed_rows, "skipped": skipped, "total": len(items)}


# ---------- 成本联动：用入库含税单价刷新 SKU 档案成本 ----------

def refresh_cost_from_inbound(db: Session, actor: str = "system") -> dict[str, Any]:
    """用入库明细的「含税单价」刷新 SKU 档案成本（default_cost）。

    口径：取该 SKU 最近一次非人工（非 manual）入库的含税单价；守门基准与兜底
    均取该 SKU 全部入库历史（含 manual 人工锁定单，价值最高）的单价中位数——
    最近一次单价偏离全历史中位数 >50% 时视为录错，改用中位数兜底而非跳过，
    避免唯一一条 price_ok 离群线把档案成本钉死在错误值（也避免录错单价污染档案）。
    """
    from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem

    rows = (
        db.query(JackyunGoodsDocumentItem, JackyunGoodsDocument)
        .join(JackyunGoodsDocument, JackyunGoodsDocumentItem.document_id == JackyunGoodsDocument.id)
        .filter(
            JackyunGoodsDocument.document_type == "inbound",
            JackyunGoodsDocumentItem.matched_sku_id.isnot(None),
            JackyunGoodsDocumentItem.match_status != "manual",
            JackyunGoodsDocumentItem.unit_price_tax.isnot(None),
        )
        .order_by(JackyunGoodsDocument.document_at.desc())
        .all()
    )
    by_sku: dict[int, list[tuple[JackyunGoodsDocumentItem, JackyunGoodsDocument]]] = {}
    for item, doc in rows:
        try:
            price = Decimal(str(item.unit_price_tax))
        except Exception:
            continue
        if price <= 0:
            continue
        by_sku.setdefault(int(item.matched_sku_id), []).append((item, doc))

    updated, skipped, unchanged, median_fallback = 0, [], 0, 0
    for sku_id, pairs in by_sku.items():
        latest_item, latest_doc = pairs[0]
        new_cost = Decimal(str(latest_item.unit_price_tax)).quantize(Decimal("0.0001"))
        # 守门/兜底基准 = 全部入库历史（含 manual）单价中位数，非仅非人工线
        med = _sku_unit_price_median(db, sku_id)
        if med is not None and med > 0 and abs(new_cost - med) > med * Decimal("0.5"):
            # 最近一次非人工单价是离群值 → 用全历史中位数兜底，而不是跳过不修
            new_cost = med.quantize(Decimal("0.0001"))
            median_fallback += 1
        sku = db.get(ProductSku, sku_id)
        if not sku:
            skipped.append({"skuId": sku_id, "itemId": latest_item.id, "reason": "SKU 不存在"})
            continue
        old_cost = sku.default_cost
        if old_cost is not None and abs(Decimal(str(old_cost)) - new_cost) < Decimal("0.0001"):
            unchanged += 1
            continue
        sku.default_cost = new_cost
        latest_item.match_note = (
            f"成本联动刷新：档案成本 {old_cost} → {new_cost}（{latest_doc.goodsdoc_no} 含税单价）"
            + ("；该单单价偏离全历史中位数，已取中位数兜底" if median_fallback and old_cost is not None and new_cost == med.quantize(Decimal("0.0001")) else "")
        )
        updated += 1
    db.commit()
    audit(db, actor, "purchase.sku_cost.refresh_from_inbound", "product_skus", 0,
          {"updated": updated, "unchanged": unchanged, "medianFallback": median_fallback, "skipped": len(skipped)})
    return {"ok": True, "updated": updated, "unchanged": unchanged, "medianFallback": median_fallback, "skipped": skipped}
