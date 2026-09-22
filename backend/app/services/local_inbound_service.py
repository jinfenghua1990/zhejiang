"""本系统采购入库。

采购入库的业务主单在本系统创建，吉客云入库数据只作为历史/外部参考保留。
为了兼容既有库存、成本和采购链路，这里继续复用入库单表，但通过 raw.source
明确区分本系统单据与外部导入单据。
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import re
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import settings
from app.models.alibaba1688_import import Alibaba1688Order
from app.models.catalog import ProductSku, Warehouse
from app.models.consumable import InboundConsumableUsage
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.models.procurement_chain import ProcurementChainLink
from app.models.purchase import ExternalPurchaseOrder, PurchaseAllocationItem
from app.services.procurement_chain_service import _source_pairs, is_reference_only_external_po
from app.services.warehouse_service import display_code
from app.utils.money import to_decimal


def _resolve_order(db: Session, order_id: int) -> tuple[Alibaba1688Order | None, ExternalPurchaseOrder]:
    for source, external in _source_pairs(db):
        candidate_id = source.id if source is not None else -external.id
        if candidate_id == order_id and external is not None:
            if is_reference_only_external_po(external):
                raise ValueError("该订单只是外部入库参考记录，不能创建本系统采购入库")
            return source, external
    raise ValueError("采购订单不存在或已删除")


def _number(db: Session, raw: str | None, inbound_at: datetime | None) -> str:
    value = (raw or "").strip()
    if value:
        if len(value) > 128:
            raise ValueError("本系统入库单号不能超过 128 个字符")
        return value
    tz = ZoneInfo(settings.TZ)
    local_date = inbound_at or datetime.now(tz)
    if local_date.tzinfo is not None:
        local_date = local_date.astimezone(tz)
    prefix = f"RK{local_date:%Y%m%d}"
    existing = db.query(JackyunGoodsDocument.goodsdoc_no).filter(
        JackyunGoodsDocument.document_type == "inbound",
        JackyunGoodsDocument.goodsdoc_no.like(f"{prefix}%"),
    ).all()
    used = {
        int(match.group(1))
        for (number,) in existing
        if (match := re.fullmatch(rf"{re.escape(prefix)}(\d{{4}})", number or ""))
    }
    sequence = max(used, default=0) + 1
    if sequence > 9999:
        raise ValueError("当天自动入库单号已超过 9999 张，请手动填写入库单号")
    return f"{prefix}{sequence:04d}"




def _allocation_receipt_states(db: Session, allocation_ids: list[int]) -> dict[int, dict]:
    """从本系统入库明细事实计算采购分配行累计已入库数量。"""
    ids = sorted(set(int(value) for value in allocation_ids))
    states = {
        allocation_id: {
            "quantity": Decimal("0"),
            "latestDocumentId": None,
            "baseSource": None,
            "baseNote": None,
        }
        for allocation_id in ids
    }
    if not ids:
        return states
    items = (
        db.query(JackyunGoodsDocumentItem)
        .filter(
            JackyunGoodsDocumentItem.raw["sourceAllocationId"].astext.in_(
                [str(value) for value in ids]
            )
        )
        .order_by(JackyunGoodsDocumentItem.id)
        .all()
    )
    document_ids = {item.document_id for item in items}
    documents = (
        {
            row.id: row
            for row in db.query(JackyunGoodsDocument)
            .filter(
                JackyunGoodsDocument.id.in_(document_ids),
                JackyunGoodsDocument.document_type == "inbound",
            )
            .all()
        }
        if document_ids
        else {}
    )
    for item in items:
        raw = item.raw if isinstance(item.raw, dict) else {}
        try:
            allocation_id = int(raw.get("sourceAllocationId"))
        except (TypeError, ValueError):
            continue
        state = states.get(allocation_id)
        document = documents.get(item.document_id)
        document_raw = document.raw if document is not None and isinstance(document.raw, dict) else {}
        if state is None or document_raw.get("source") != "local_purchase_inbound":
            continue
        state["quantity"] += to_decimal(item.quantity)
        state["latestDocumentId"] = max(
            int(state["latestDocumentId"] or 0), int(item.document_id)
        )
        if state["baseSource"] is None:
            state["baseSource"] = str(
                raw.get("baseAllocationSource")
                or raw.get("previousAllocationSource")
                or "manual"
            )
            state["baseNote"] = str(
                raw.get("baseAllocationNote")
                if raw.get("baseAllocationNote") is not None
                else raw.get("previousAllocationNote")
                or ""
            )
    return states


def _allocation_inbound_note(received: Decimal, ordered: Decimal, latest_document_id: int) -> str:
    if received >= ordered:
        return f"由入库单 #{latest_document_id} 明细自动反填"
    return f"累计入库 {received}/{ordered}；最近入库单 #{latest_document_id}"


def create_purchase_inbound(
    db: Session,
    *,
    order_id: int,
    inbound_no: str = "",
    inbound_at: datetime | None = None,
    warehouse_id: int | None = None,
    items: list[dict],
    note: str = "",
    actor: str = "system",
) -> tuple[JackyunGoodsDocument, ProcurementChainLink]:
    source, external = _resolve_order(db, order_id)
    if not items:
        raise ValueError("请至少选择一条采购入库明细")

    number = _number(db, inbound_no, inbound_at)
    exists = db.query(JackyunGoodsDocument).filter_by(
        document_type="inbound", goodsdoc_no=number
    ).first()
    if exists is not None:
        raise ValueError(f"入库单号 {number} 已存在，请更换本系统入库单号")

    warehouse = None
    if warehouse_id is not None:
        warehouse = db.get(Warehouse, warehouse_id)
        if warehouse is None or warehouse.status != "active":
            raise ValueError("所选仓库不存在或已停用")
        if warehouse.purpose not in {"goods", "both"}:
            raise ValueError("所选仓库不能承接正品入库")

    allocation_ids = [int(item.get("allocation_id")) for item in items]
    if len(set(allocation_ids)) != len(allocation_ids):
        raise ValueError("同一采购明细不能重复入库")
    allocations = (
        db.query(PurchaseAllocationItem)
        .filter(
            PurchaseAllocationItem.po_id == external.id,
            PurchaseAllocationItem.id.in_(allocation_ids),
        )
        .with_for_update()
        .all()
    )
    by_id = {row.id: row for row in allocations}
    if len(by_id) != len(allocation_ids):
        raise ValueError("存在不属于当前采购单的入库明细")
    receipt_states = _allocation_receipt_states(db, allocation_ids)

    sku_ids = {row.sku_id for row in allocations if row.sku_id is not None}
    sku_rows = db.query(ProductSku).filter(ProductSku.id.in_(sku_ids)).all() if sku_ids else []
    sku_by_id = {row.id: row for row in sku_rows}
    item_rows: list[JackyunGoodsDocumentItem] = []
    total_quantity = Decimal("0")
    total_amount = Decimal("0")
    for line_no, payload in enumerate(items, start=1):
        allocation = by_id[int(payload["allocation_id"])]
        if allocation.sku_id is None or allocation.sku_id not in sku_by_id:
            raise ValueError(f"采购明细 {allocation.goods_name or allocation.sku_code} 尚未绑定有效 SKU")
        quantity = to_decimal(payload.get("quantity"))
        if not quantity.is_finite() or quantity <= 0:
            raise ValueError("入库数量必须大于 0")
        ordered_quantity = to_decimal(allocation.quantity)
        already_received = receipt_states.get(allocation.id, {}).get("quantity", Decimal("0"))
        remaining_quantity = max(ordered_quantity - already_received, Decimal("0"))
        if remaining_quantity <= 0:
            raise ValueError(f"{allocation.goods_name or allocation.sku_code} 已全部入库")
        if quantity > remaining_quantity:
            raise ValueError(
                f"{allocation.goods_name or allocation.sku_code} 本次入库数量不能超过剩余可入库数量 {remaining_quantity}"
            )
        unit_price = to_decimal(payload.get("unit_price")) if payload.get("unit_price") not in (None, "") else to_decimal(allocation.unit_price)
        if not unit_price.is_finite() or unit_price < 0:
            raise ValueError("入库含税单价不能为负数")
        sku = sku_by_id[allocation.sku_id]
        amount = (quantity * unit_price).quantize(Decimal("0.0001"))
        item_rows.append(JackyunGoodsDocumentItem(
            line_no=line_no,
            goods_no=sku.sku_code or allocation.sku_code or "",
            sku_barcode=sku.barcode or "",
            goods_name=sku.sku_name or allocation.goods_name or "",
            quantity=quantity,
            unit_name=sku.unit or "",
            unit_price_tax=unit_price,
            amount_tax=amount,
            matched_sku_id=sku.id,
            match_status="manual",
            match_note="本系统采购入库创建",
            raw={
                "sourceAllocationId": allocation.id,
                "previousAllocationSource": allocation.source or "manual",
                "previousAllocationNote": allocation.note or "",
                "baseAllocationSource": (
                    receipt_states.get(allocation.id, {}).get("baseSource")
                    or (allocation.source if allocation.source != "inbound_auto" else "manual")
                    or "manual"
                ),
                "baseAllocationNote": (
                    receipt_states.get(allocation.id, {}).get("baseNote")
                    if receipt_states.get(allocation.id, {}).get("baseNote") is not None
                    else (allocation.note or "")
                ),
            },
        ))
        total_quantity += quantity
        total_amount += amount

    document = JackyunGoodsDocument(
        document_type="inbound",
        goodsdoc_no=number,
        document_at=inbound_at or datetime.now(timezone.utc),
        warehouse_code=display_code(warehouse) if warehouse else "",
        warehouse_name=warehouse.name if warehouse else "",
        company_name="",
        supplier_name=external.supplier_name or (source.seller_company_name if source else ""),
        total_quantity=total_quantity,
        total_amount=total_amount,
        total_fee=Decimal("0"),
        raw={
            "source": "local_purchase_inbound",
            "platform": external.platform or "other",
            "platformPurchaseOrderNo": external.external_order_id,
            "externalReferenceNo": external.external_order_id,
            "externalPurchaseOrderId": external.id,
            "sourceOrderId": source.id if source else None,
            "note": note.strip(),
            "createdBy": actor,
        },
    )
    db.add(document)
    db.flush()
    from app.services.supplier_sync_service import ensure_supplier
    ensure_supplier(db, document.supplier_name, platform=external.platform or "其他")
    for item in item_rows:
        item.document_id = document.id
        db.add(item)
    added_by_allocation = {
        int(payload["allocation_id"]): to_decimal(payload.get("quantity"))
        for payload in items
    }
    for allocation in allocations:
        ordered_quantity = to_decimal(allocation.quantity)
        received_before = receipt_states.get(allocation.id, {}).get("quantity", Decimal("0"))
        received_after = received_before + added_by_allocation.get(allocation.id, Decimal("0"))
        allocation.source = "inbound_auto"
        allocation.note = _allocation_inbound_note(received_after, ordered_quantity, document.id)

    identity = {"order_id": source.id} if source is not None else {"external_po_id": external.id}
    link = ProcurementChainLink(
        **identity,
        target_type="inbound",
        target_id=document.id,
        match_method="local",
        confidence=Decimal("1"),
        confirmed=True,
        note=f"本系统采购入库：{number}（平台采购单 {external.external_order_id}）",
    )
    db.add(link)
    db.flush()
    from app.services import finance_projection_service
    finance_projection_service.project_inbound_document(db, document)
    db.commit()
    return document, link


def delete_local_purchase_inbound(
    db: Session,
    *,
    document_id: int,
    commit: bool = True,
) -> dict:
    """删除本系统创建的采购入库，并在同一事务内撤销其副作用。"""
    document = db.get(JackyunGoodsDocument, document_id)
    if document is None or document.document_type != "inbound":
        raise ValueError("入库单不存在")
    raw = document.raw if isinstance(document.raw, dict) else {}
    if raw.get("source") != "local_purchase_inbound":
        raise ValueError("吉客云历史入库单不能删除，只能解除关联")

    items = db.query(JackyunGoodsDocumentItem).filter(
        JackyunGoodsDocumentItem.document_id == document.id,
    ).all()
    allocation_ids: list[int] = []
    previous_allocation_values: dict[int, tuple[str, str]] = {}
    for item in items:
        item_raw = item.raw if isinstance(item.raw, dict) else {}
        allocation_id = item_raw.get("sourceAllocationId")
        if allocation_id in (None, ""):
            continue
        try:
            allocation_id = int(allocation_id)
        except (TypeError, ValueError):
            continue
        allocation_ids.append(allocation_id)
        previous_allocation_values[allocation_id] = (
            str(
                item_raw.get("baseAllocationSource")
                or item_raw.get("previousAllocationSource")
                or "manual"
            ),
            str(
                item_raw.get("baseAllocationNote")
                if item_raw.get("baseAllocationNote") is not None
                else item_raw.get("previousAllocationNote")
                or ""
            ),
        )

    links = db.query(ProcurementChainLink).filter(
        ProcurementChainLink.target_type == "inbound",
        ProcurementChainLink.target_id == document.id,
    ).all()
    link_ids = {link.id for link in links}

    # 兼容采购单先被删除、链路误删但耗材使用记录仍在的历史脏数据。
    orphan_usage_links = db.query(InboundConsumableUsage.link_id).filter(
        InboundConsumableUsage.inbound_document_id == document.id,
    ).all()
    link_ids.update(link_id for (link_id,) in orphan_usage_links)
    from app.services.consumable_service import _reverse_inbound_usage
    for link_id in sorted(link_ids):
        _reverse_inbound_usage(db, link_id)
    db.flush()

    # _reverse_inbound_usage 已删除正常/孤立 usage；再按 document_id 清理残留，
    # 确保删除后不会留下孤儿记录。
    db.query(InboundConsumableUsage).filter(
        InboundConsumableUsage.inbound_document_id == document.id,
    ).delete(synchronize_session=False)
    db.query(ProcurementChainLink).filter(
        ProcurementChainLink.target_type == "inbound",
        ProcurementChainLink.target_id == document.id,
    ).delete(synchronize_session=False)
    db.query(JackyunGoodsDocumentItem).filter(
        JackyunGoodsDocumentItem.document_id == document.id,
    ).delete(synchronize_session=False)
    db.flush()
    if allocation_ids:
        allocations = (
            db.query(PurchaseAllocationItem)
            .filter(PurchaseAllocationItem.id.in_(allocation_ids))
            .with_for_update()
            .all()
        )
        remaining_states = _allocation_receipt_states(db, allocation_ids)
        for allocation in allocations:
            state = remaining_states.get(allocation.id, {})
            received = state.get("quantity", Decimal("0"))
            if received > 0:
                allocation.source = "inbound_auto"
                allocation.note = _allocation_inbound_note(
                    received,
                    to_decimal(allocation.quantity),
                    int(state.get("latestDocumentId") or 0),
                )
            else:
                source, note = previous_allocation_values.get(allocation.id, ("manual", ""))
                allocation.source = "manual" if source == "inbound_auto" else source
                allocation.note = note
    inbound_no = document.goodsdoc_no
    from app.services import finance_projection_service
    finance_projection_service.delete_projected_source(
        db, "domestic_inbound", str(document.id)
    )
    db.delete(document)
    if commit:
        db.commit()
    else:
        db.flush()
    return {
        "ok": True,
        "documentId": document_id,
        "inboundNo": inbound_no,
        "removedLinkIds": sorted(link_ids),
        "restoredAllocationIds": sorted(set(allocation_ids)),
    }
