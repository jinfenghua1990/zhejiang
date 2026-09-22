"""采购链路数据修复：只处理可明确判定的历史关系。"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.audit import audit
from app.models.jackyun import JackyunGoodsDocumentItem
from app.models.procurement_chain import ProcurementChainLink
from app.models.purchase import ExternalPurchaseOrder, PurchaseAllocationItem
from app.services.procurement_chain_service import (
    is_reference_only_external_po,
    parse_inbound_doc_id,
)


def _text(value: object) -> str:
    return str(value or "").strip()


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


def _allocation_item_candidates(
    row: PurchaseAllocationItem,
    po: ExternalPurchaseOrder,
    items_by_doc: dict[int, list[JackyunGoodsDocumentItem]],
) -> list[JackyunGoodsDocumentItem]:
    """Return only an exact, row-level match from the inbound file marker.

    The marker is written by the Jackyun inbound import as ``_1688采购订单``.
    Quantity and price are deliberately checked too: an order number alone is
    not enough when a document contains repeated SKUs or split receipts.
    """
    doc_id = parse_inbound_doc_id(row.note)
    if doc_id is None or not po.external_order_id:
        return []
    order_no = _text(po.external_order_id)
    candidates: list[JackyunGoodsDocumentItem] = []
    for item in items_by_doc.get(doc_id, []):
        raw = item.raw or {}
        if _text(raw.get("_1688采购订单")) != order_no:
            continue
        sku_match = (
            row.sku_id is not None
            and item.matched_sku_id == row.sku_id
        ) or (
            _text(row.sku_code) in {_text(item.sku_barcode), _text(item.goods_no)}
        )
        if not sku_match:
            continue
        if abs(_decimal(row.quantity) - _decimal(item.quantity)) > Decimal("0.0001"):
            continue
        if abs(_decimal(row.unit_price) - _decimal(item.unit_price_tax)) > Decimal("0.0001"):
            continue
        candidates.append(item)
    return candidates


def repair_unambiguous_inbound_allocation_sources(
    db: Session,
    actor: str = "system",
) -> dict[str, int]:
    """Backfill exact historical ``source_item_id`` pointers.

    Older imports created ``inbound_auto`` rows with only a document note;
    some later manual edits preserved the same auto-generated note but lost the
    source pointer. If the inbound line itself carries one unique external
    order marker and the quantity/price/SKU also match, the pointer can be
    restored safely. Rows without that evidence remain untouched for manual review.
    """
    candidate_rows = (
        db.query(PurchaseAllocationItem)
        .order_by(PurchaseAllocationItem.id)
        .all()
    )
    rows = [
        row for row in candidate_rows
        if row.source == "inbound_auto"
        or (row.source == "manual" and parse_inbound_doc_id(row.note) is not None)
    ]
    pos = {
        po.id: po
        for po in db.query(ExternalPurchaseOrder).all()
        if not is_reference_only_external_po(po)
    }
    items_by_doc: dict[int, list[JackyunGoodsDocumentItem]] = defaultdict(list)
    for item in db.query(JackyunGoodsDocumentItem).all():
        items_by_doc[item.document_id].append(item)

    proposed: dict[int, int] = {}
    ambiguous = 0
    for row in rows:
        po = pos.get(row.po_id)
        candidates = _allocation_item_candidates(row, po, items_by_doc) if po else []
        if len(candidates) == 1 and candidates[0].id != row.source_item_id:
            proposed[row.id] = candidates[0].id
        elif len(candidates) > 1:
            ambiguous += 1

    # A wrongly assigned historical pointer must not block its own exact
    # replacement.  Only pointers that are not also being repaired block a
    # candidate; duplicate proposed targets are treated as ambiguous.
    proposed_targets = list(proposed.values())
    target_counts = {target: proposed_targets.count(target) for target in set(proposed_targets)}
    current_claimers: dict[int, list[PurchaseAllocationItem]] = defaultdict(list)
    for row in rows:
        if row.source_item_id is not None:
            current_claimers[row.source_item_id].append(row)

    repaired = 0
    skipped_claimed = 0
    for row in rows:
        target_id = proposed.get(row.id)
        if target_id is None:
            continue
        if target_counts[target_id] > 1:
            ambiguous += 1
            continue
        blockers = [
            claimant for claimant in current_claimers.get(target_id, [])
            if claimant.id != row.id and claimant.id not in proposed
        ]
        if blockers:
            skipped_claimed += 1
            continue
        before = row.source_item_id
        row.source_item_id = target_id
        db.commit()
        audit(
            db,
            actor,
            "purchase.allocation.repair_source_item",
            "purchase_allocation_items",
            row.id,
            {
                "poId": row.po_id,
                "orderNo": pos[row.po_id].external_order_id,
                "beforeSourceItemId": before,
                "sourceItemId": target_id,
                "reason": "入库明细原始订单标记、SKU、数量和单价唯一一致",
            },
        )
        repaired += 1

    return {
        "scanned": len(rows),
        "repaired": repaired,
        "ambiguous": ambiguous,
        "skippedClaimed": skipped_claimed,
    }


def repair_reference_only_links(db: Session, actor: str = "system") -> dict[str, int]:
    """解除入库申请单本地参考编号造成的自动采购关系。

    原始入库单、导入批次和采购主档副本都保留；只拒绝 ``file_import``/``auto``
    自动关系，并释放尚未人工编辑的入库反填行。人工关系和人工分配不碰。
    """
    from app.services.consumable_service import _reverse_inbound_usage
    from app.services.inbound_allocation_seed import release_inbound_seeds_for_link

    reference_pos = [
        po for po in db.query(ExternalPurchaseOrder).all()
        if is_reference_only_external_po(po)
    ]
    repaired_links = 0
    released_allocations = 0
    reversed_usage_links = 0
    repaired_pos = 0

    for po in reference_pos:
        links = db.query(ProcurementChainLink).filter(
            ProcurementChainLink.external_po_id == po.id,
            ProcurementChainLink.target_type == "inbound",
            ProcurementChainLink.match_method.in_(("file_import", "auto")),
        ).all()
        if not links:
            continue

        link_ids: list[int] = []
        for link in links:
            if link.consumable_usage_enabled is True:
                _reverse_inbound_usage(db, link.id)
                reversed_usage_links += 1
            released = release_inbound_seeds_for_link(db, link, actor=actor)
            released_allocations += int(released.get("released", 0))

            original_note = link.note or ""
            link.confirmed = False
            link.match_method = "rejected"
            link.confidence = None
            link.consumable_usage_decided = False
            link.consumable_usage_enabled = None
            repair_note = "系统修复：入库申请单本地参考编号未作为真实采购主单，入库单保留待重新关联"
            link.note = f"{original_note}；{repair_note}" if original_note else repair_note
            db.commit()
            audit(
                db,
                actor,
                "purchase.chain.repair_reference_only",
                "procurement_chain_links",
                link.id,
                {
                    "externalPoId": po.id,
                    "orderNo": po.external_order_id,
                    "targetId": link.target_id,
                    "releasedAllocations": int(released.get("released", 0)),
                },
            )
            repaired_links += 1
            link_ids.append(link.id)

        po.raw = {
            **(po.raw or {}),
            "relationRepair": {
                "status": "unlinked",
                "repairedAt": datetime.now(timezone.utc).isoformat(),
                "linkIds": link_ids,
                "reason": "本地入库申请单编号不是真实外部采购订单号",
            },
        }
        db.commit()
        audit(
            db,
            actor,
            "purchase.po.reference_only_repaired",
            "external_purchase_orders",
            po.id,
            {"orderNo": po.external_order_id, "linkIds": link_ids},
        )
        repaired_pos += 1

    return {
        "referencePurchaseOrders": len(reference_pos),
        "repairedPurchaseOrders": repaired_pos,
        "repairedLinks": repaired_links,
        "releasedAllocations": released_allocations,
        "reversedUsageLinks": reversed_usage_links,
    }
