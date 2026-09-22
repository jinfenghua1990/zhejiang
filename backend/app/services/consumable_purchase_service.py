"""耗材采购只在实际收货时增加库存；一张单可分批收货。"""

import hashlib
import json
from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.alibaba1688_import import Alibaba1688FileImport, Alibaba1688Order
from app.models.consumable import Consumable, ConsumableTransaction
from app.models.consumable_purchase import ConsumablePurchase, ConsumablePurchaseItem, ConsumableReceipt
from app.services.consumable_service import record_transaction
from app.utils.money import quantize, to_decimal


def fingerprint(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def build_consumable_purchase_number(
    db: Session,
    *,
    ordered_on: date,
    reference_no: str = "",
    source_order_id: int | None = None,
) -> str:
    """生成耗材采购入库主单号：有平台订单号时固定以 HC+平台订单号为主。"""
    source = db.get(Alibaba1688Order, source_order_id) if source_order_id is not None else None
    platform_order_no = reference_no.strip() or (source.external_order_id if source else "")
    if platform_order_no:
        base = f"HC{platform_order_no}"
        if len(base) > 40:
            raise ValueError("平台采购单号过长，无法生成 HC 入库单号")
    else:
        base = f"HC{ordered_on:%Y%m%d}-{uuid4().hex[:8].upper()}"

    candidate = base
    suffix = 2
    while db.query(ConsumablePurchase.id).filter(ConsumablePurchase.number == candidate).first() is not None:
        marker = f"-{suffix}"
        candidate = f"{base[:40 - len(marker)]}{marker}"
        suffix += 1
    return candidate


def source_orders(db: Session, search: str = "") -> list[dict]:
    query = db.query(Alibaba1688Order).join(Alibaba1688FileImport, Alibaba1688Order.import_id == Alibaba1688FileImport.id).filter(
        Alibaba1688Order.row_status == "active", Alibaba1688FileImport.lifecycle == "active",
    )
    if search.strip():
        pattern = f"%{search.strip()}%"
        query = query.filter(or_(Alibaba1688Order.external_order_id.ilike(pattern), Alibaba1688Order.seller_company_name.ilike(pattern)))
    return [{"id": row.id, "orderNo": row.external_order_id, "supplierName": row.seller_company_name or row.seller_member_name}
            for row in query.order_by(Alibaba1688Order.order_time.desc().nullslast(), Alibaba1688Order.id.desc()).limit(100)]


def serialize_purchase(db: Session, row: ConsumablePurchase, detail: bool = False) -> dict:
    lines = db.query(ConsumablePurchaseItem).filter_by(purchase_id=row.id).order_by(ConsumablePurchaseItem.id).all()
    source = db.get(Alibaba1688Order, row.source_order_id) if row.source_order_id else None
    result = {
        "id": row.id, "number": row.number, "supplierName": row.supplier_name,
        "orderedOn": row.ordered_on.isoformat(), "sourceOrderId": row.source_order_id,
        "sourceOrderNo": source.external_order_id if source else None,
        "referenceNo": row.reference_no, "status": row.status, "note": row.note,
        "amount": f"{sum((quantize(line.quantity * line.unit_cost) for line in lines), Decimal('0')):.4f}",
        "receivedAmount": f"{sum((quantize(line.received_qty * line.unit_cost) for line in lines), Decimal('0')):.4f}",
        "items": [{"id": line.id, "consumableId": line.consumable_id, "code": line.code, "name": line.name, "unit": line.unit,
                   "quantity": str(line.quantity), "receivedQty": str(line.received_qty), "unitCost": str(line.unit_cost)} for line in lines],
    }
    if detail:
        receipts = db.query(ConsumableReceipt).filter_by(purchase_id=row.id).order_by(ConsumableReceipt.id.desc()).all()
        txs = db.query(ConsumableTransaction).filter(
            ConsumableTransaction.source_type == "consumable_receipt",
            ConsumableTransaction.source_id.in_([receipt.id for receipt in receipts]),
        ).all() if receipts else []
        line_by_material = {line.consumable_id: line for line in lines}
        result["receipts"] = [{
            "id": receipt.id, "number": receipt.number, "receivedOn": receipt.received_on.isoformat(),
            "location": receipt.location or "own",
            "note": receipt.note, "createdBy": receipt.created_by,
            "items": [{"consumableId": tx.consumable_id, "quantity": str(tx.quantity),
                       "name": line_by_material[tx.consumable_id].name, "unit": line_by_material[tx.consumable_id].unit}
                      for tx in txs if tx.source_id == receipt.id],
        } for receipt in receipts]
    return result


def list_purchases(db: Session, search: str = "", source_order_id: int | None = None,
                   order_no: str = "") -> list[dict]:
    query = db.query(ConsumablePurchase)
    if source_order_id is not None:
        query = query.filter_by(source_order_id=source_order_id)
    if order_no.strip():
        # 按订单号精确圈定本单的耗材入库单：1688 单看 source 原件的单号，
        # 工作流独有单（无 1688 原件）看 reference_no——否则会漏出其他订单的 HC 单。
        pattern = order_no.strip()
        query = query.outerjoin(Alibaba1688Order, Alibaba1688Order.id == ConsumablePurchase.source_order_id).filter(or_(
            ConsumablePurchase.reference_no == pattern, Alibaba1688Order.external_order_id == pattern,
        ))
    if search.strip():
        pattern = f"%{search.strip()}%"
        query = query.outerjoin(Alibaba1688Order, Alibaba1688Order.id == ConsumablePurchase.source_order_id).filter(or_(
            ConsumablePurchase.number.ilike(pattern), ConsumablePurchase.supplier_name.ilike(pattern),
            ConsumablePurchase.reference_no.ilike(pattern), Alibaba1688Order.external_order_id.ilike(pattern),
        ))
    return [serialize_purchase(db, row) for row in query.order_by(ConsumablePurchase.id.desc()).limit(200)]


def create_purchase(db: Session, *, request_key: str, supplier_name: str, ordered_on: date,
                    items: list[dict], source_order_id: int | None = None, reference_no: str = "", note: str = "", actor: str = "") -> ConsumablePurchase:
    supplier_name, reference_no, note = supplier_name.strip(), reference_no.strip(), note.strip()
    if not supplier_name or not items:
        raise ValueError("请填写供应商和至少一条耗材")
    if len({item["consumable_id"] for item in items}) != len(items):
        raise ValueError("同一耗材请合并成一行")
    payload = dict(supplier_name=supplier_name, ordered_on=ordered_on, source_order_id=source_order_id, reference_no=reference_no, note=note, items=items)
    digest = fingerprint(payload)
    existing = db.query(ConsumablePurchase).filter_by(request_key=request_key).first()
    if existing:
        if existing.request_fingerprint != digest:
            raise ValueError("这次提交已保存过不同内容，请刷新后重新操作")
        return existing
    if source_order_id is not None:
        source = db.get(Alibaba1688Order, source_order_id)
        source_import = db.get(Alibaba1688FileImport, source.import_id) if source else None
        if not source or source.row_status != "active" or not source_import or source_import.lifecycle != "active":
            raise ValueError("关联的 1688 订单不存在或已移除")
    materials = {row.id: row for row in db.query(Consumable).filter(Consumable.id.in_([i["consumable_id"] for i in items])).all()}
    for item in items:
        material = materials.get(item["consumable_id"])
        if not material or material.status != "active":
            raise ValueError("耗材不存在或已停用")
        if item["quantity"] <= 0 or item["unit_cost"] < 0:
            raise ValueError("采购数量必须大于 0，单价不能为负")
    from app.services.supplier_sync_service import ensure_supplier
    ensure_supplier(db, supplier_name, platform="1688" if source_order_id is not None else "线下")
    number = build_consumable_purchase_number(
        db, ordered_on=ordered_on, reference_no=reference_no, source_order_id=source_order_id,
    )
    row_id = db.scalar(insert(ConsumablePurchase).values(
        number=number, request_key=request_key,
        request_fingerprint=digest, supplier_name=supplier_name, ordered_on=ordered_on,
        source_order_id=source_order_id, reference_no=reference_no, status="ordered", note=note, created_by=actor,
    ).on_conflict_do_nothing().returning(ConsumablePurchase.id))
    if row_id is None:
        existing = db.query(ConsumablePurchase).filter_by(request_key=request_key).first()
        if existing and existing.request_fingerprint == digest:
            return existing
        raise ValueError("该 1688 订单已有关联耗材采购单，请打开原单继续收货")
    row = db.get(ConsumablePurchase, row_id)
    for item in items:
        material = materials[item["consumable_id"]]
        db.add(ConsumablePurchaseItem(purchase_id=row.id, **item, received_qty=Decimal("0"), code=material.code, name=material.name, unit=material.unit))
    db.commit()
    return row


def receive_purchase(
    db: Session,
    purchase_id: int,
    *,
    request_key: str,
    received_on: date,
    items: list[dict],
    note: str = "",
    actor: str = "",
    location: str = "own",
    warehouse_id: int | None = None,
) -> ConsumablePurchase:
    row = db.scalar(select(ConsumablePurchase).where(ConsumablePurchase.id == purchase_id).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise ValueError("耗材采购单不存在")
    if location not in {"own", "factory"}:
        raise ValueError("到货位置必须是 own（自有仓）或 factory（工厂）")
    digest = fingerprint(dict(
        purchase_id=purchase_id,
        received_on=received_on,
        items=items,
        note=note.strip(),
        location=location,
        warehouse_id=warehouse_id,
    ))
    existing = db.query(ConsumableReceipt).filter_by(request_key=request_key).first()
    if existing:
        if existing.request_fingerprint != digest:
            raise ValueError("这次收货已登记过不同内容，请刷新后重新操作")
        return row
    if row.status not in {"ordered", "partial"}:
        raise ValueError("该采购单已收齐或已取消")
    if received_on < row.ordered_on:
        raise ValueError("收货日期不能早于采购日期")
    if not items or len({item["item_id"] for item in items}) != len(items):
        raise ValueError("请填写不重复的收货明细")
    lines = db.query(ConsumablePurchaseItem).filter_by(purchase_id=purchase_id).populate_existing().all()
    by_id = {line.id: line for line in lines}
    for item in items:
        line = by_id.get(item["item_id"])
        if line is None or item["quantity"] <= 0:
            raise ValueError("收货明细或数量不正确")
        if line.received_qty + item["quantity"] > line.quantity:
            raise ValueError(f"{line.name} 的收货数量超过待收数量")
    # 同一次收货的所有耗材按固定顺序加锁，库存与收货明细一并提交。
    material_ids = sorted(by_id[item["item_id"]].consumable_id for item in items)
    db.scalars(select(Consumable).where(Consumable.id.in_(material_ids)).order_by(Consumable.id).with_for_update().execution_options(populate_existing=True)).all()
    receipt = ConsumableReceipt(
        purchase_id=purchase_id, number=f"HR{received_on:%Y%m%d}-{uuid4().hex[:8].upper()}", request_key=request_key,
        request_fingerprint=digest, received_on=received_on, warehouse_id=warehouse_id,
        location=location, note=note.strip(), created_by=actor,
    )
    db.add(receipt)
    db.flush()
    for item in items:
        line = by_id[item["item_id"]]
        record_transaction(db, consumable_id=line.consumable_id, transaction_type="purchase", quantity=str(item["quantity"]),
                           unit_cost=str(line.unit_cost), source_type="consumable_receipt", source_id=receipt.id,
                           location=location, warehouse_id=warehouse_id,
                           note=f"{row.number} / {receipt.number} {note.strip()}".strip(), commit=False)
        line.received_qty += item["quantity"]
    row.status = "received" if all(line.received_qty == line.quantity for line in lines) else "partial"
    db.commit()
    return row


def cancel_purchase(db: Session, purchase_id: int) -> ConsumablePurchase:
    row = db.scalar(select(ConsumablePurchase).where(ConsumablePurchase.id == purchase_id).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise ValueError("耗材采购单不存在")
    if row.status not in {"ordered", "cancelled"}:
        raise ValueError("已有收货记录的采购单不能取消")
    row.status = "cancelled"
    db.commit()
    return row


def reopen_purchase(db: Session, purchase_id: int) -> ConsumablePurchase:
    """把 cancelled 状态的采购单恢复为 ordered（仅对未收货的开放）。"""
    row = db.scalar(select(ConsumablePurchase).where(ConsumablePurchase.id == purchase_id).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise ValueError("耗材采购单不存在")
    if row.status != "cancelled":
        raise ValueError("只有已取消的采购单可恢复")
    if db.query(ConsumablePurchaseItem).filter(
        ConsumablePurchaseItem.purchase_id == purchase_id,
        ConsumablePurchaseItem.received_qty > 0,
    ).count() > 0:
        raise ValueError("已收过货的采购单不能恢复（避免台账与状态不一致）")
    row.status = "ordered"
    db.commit()
    return row


def update_purchase(db: Session, purchase_id: int, *, supplier_name: str | None = None,
                    ordered_on: date | None = None, note: str | None = None,
                    items: list[dict] | None = None) -> ConsumablePurchase:
    """编辑耗材采购单（含已收货的单——录错了要能改，不能写死）。

    - supplier_name / ordered_on / note：直接覆盖；
    - items：按 consumable_id 全量对齐（前端整体 PATCH），删旧 + 增新；
      * 单价：随时可改采购单；已发生收货流水保留当时成本快照，不回写历史事实；
      * 数量：不能小于该行已收数量（小于 = 先删整单重录，收货流水不能凭空缩水）；
      * 删行：仅未收货的行可删；
      * 收货后改数量会重算状态（received ↔ partial）；
    - 编辑 cancelled 单自动恢复 ordered。
    """
    row = db.scalar(select(ConsumablePurchase).where(ConsumablePurchase.id == purchase_id).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise ValueError("耗材采购单不存在")
    if row.status not in {"ordered", "cancelled", "partial", "received"}:
        raise ValueError("当前状态不允许编辑")

    if supplier_name is not None:
        trimmed = supplier_name.strip()
        if not trimmed:
            raise ValueError("供应商不能为空")
        row.supplier_name = trimmed
    if ordered_on is not None:
        earliest_receipt = (
            db.query(ConsumableReceipt)
            .filter_by(purchase_id=purchase_id)
            .order_by(ConsumableReceipt.received_on.asc(), ConsumableReceipt.id.asc())
            .first()
        )
        if earliest_receipt is not None and ordered_on > earliest_receipt.received_on:
            raise ValueError(
                f"采购日期不能晚于已登记收货日期 {earliest_receipt.received_on.isoformat()}"
            )
        row.ordered_on = ordered_on
    if note is not None:
        row.note = note.strip()

    if items is not None:
        if not items:
            raise ValueError("至少保留一条耗材明细")
        if len({int(i["consumable_id"]) for i in items}) != len(items):
            raise ValueError("同一耗材请合并成一行")
        existing_items = db.query(ConsumablePurchaseItem).filter_by(purchase_id=purchase_id).populate_existing().all()
        for it in items:
            if Decimal(str(it["quantity"])) <= 0:
                raise ValueError("采购数量必须大于 0")
            if Decimal(str(it["unit_cost"])) < 0:
                raise ValueError("单价不能为负")
        materials = {m.id: m for m in db.query(Consumable).filter(Consumable.id.in_([int(i["consumable_id"]) for i in items])).all()}
        for it in items:
            mat = materials.get(int(it["consumable_id"]))
            if not mat or mat.status != "active":
                raise ValueError("耗材不存在或已停用")
        by_id = {li.consumable_id: li for li in existing_items}
        seen: set[int] = set()
        for it in items:
            cid = int(it["consumable_id"])
            seen.add(cid)
            material = materials[cid]
            new_qty = Decimal(str(it["quantity"]))
            new_cost = Decimal(str(it["unit_cost"]))
            if cid in by_id:
                li = by_id[cid]
                received = Decimal(str(li.received_qty or 0))
                if new_qty < received:
                    raise ValueError(f"{li.name} 已收货 {received}，采购数量不能小于已收数量；如需减少请删除整单重录")
                li.quantity = new_qty
                li.unit_cost = new_cost
                li.code = material.code
                li.name = material.name
                li.unit = material.unit
            else:
                db.add(ConsumablePurchaseItem(
                    purchase_id=purchase_id, consumable_id=cid,
                    code=material.code, name=material.name, unit=material.unit,
                    quantity=new_qty, received_qty=Decimal("0"),
                    unit_cost=new_cost,
                ))
        for li in existing_items:
            if li.consumable_id not in seen:
                if Decimal(str(li.received_qty or 0)) > 0:
                    raise ValueError(f"{li.name} 已有收货记录，不能删除该行；如需调整请删除整单重录")
                db.delete(li)

        # 已发生的收货流水是历史成本快照；采购单改单价不得反写库存台账。
        # 收货后改了数量 → 重算状态（收齐=received，收过一部分=partial）
        refreshed = db.query(ConsumablePurchaseItem).filter_by(purchase_id=purchase_id).all()
        if any(Decimal(str(li.received_qty or 0)) > 0 for li in refreshed):
            row.status = (
                "received"
                if refreshed and all(Decimal(str(li.received_qty or 0)) >= Decimal(str(li.quantity or 0)) for li in refreshed)
                else "partial"
            )

    # 编辑即反悔取消：cancelled → ordered。
    if row.status == "cancelled":
        row.status = "ordered"
    db.commit()
    return row


def delete_purchase(db: Session, purchase_id: int) -> ConsumablePurchase:
    """物理删除耗材采购单（任何状态可删——录错了要能删，不能写死）。

    已收过货的单删除时自动冲销库存：
    - 台账 append-only：原收货流水保留，另记等量负数 manual 流水（备注冲销来源）；
    - purchased_qty 按已收数量回退，库存统计不失真；
    - 收货单（HR）随单删除，审计日志记录整单明细。
    """
    row = db.scalar(select(ConsumablePurchase).where(ConsumablePurchase.id == purchase_id).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise ValueError("耗材采购单不存在")
    receipts = db.query(ConsumableReceipt).filter_by(purchase_id=purchase_id).order_by(ConsumableReceipt.id).all()
    lines = db.query(ConsumablePurchaseItem).filter_by(purchase_id=purchase_id).all()
    if receipts:
        receipt_ids = [r.id for r in receipts]
        location_by_receipt = {r.id: (r.location or "own") for r in receipts}
        warehouse_by_receipt = {r.id: r.warehouse_id for r in receipts}
        number_by_receipt = {r.id: r.number for r in receipts}
        txs = db.query(ConsumableTransaction).filter(
            ConsumableTransaction.source_type == "consumable_receipt",
            ConsumableTransaction.source_id.in_(receipt_ids),
        ).order_by(ConsumableTransaction.id).all()
        for tx in txs:
            # 负数冲销流水：库存扣回，原流水保留（台账只增不删）
            record_transaction(
                db, consumable_id=tx.consumable_id, transaction_type="manual",
                quantity=str(-Decimal(str(tx.quantity))),
                unit_cost=str(tx.unit_cost) if tx.unit_cost is not None else None,
                source_type="consumable_receipt_reversal", source_id=tx.id,
                location=location_by_receipt.get(tx.source_id or 0, "own"),
                warehouse_id=warehouse_by_receipt.get(tx.source_id or 0),
                note=f"删除 {row.number} 冲销入库 / {number_by_receipt.get(tx.source_id or 0, '')}",
                commit=False,
            )
        # purchased_qty 回退已收数量，避免 Lifetime 统计虚高
        purchased_back: dict[int, Decimal] = {}
        for li in lines:
            got = Decimal(str(li.received_qty or 0))
            if got > 0:
                purchased_back[li.consumable_id] = purchased_back.get(li.consumable_id, Decimal("0")) + got
        for cid, qty in purchased_back.items():
            material = db.get(Consumable, cid)
            if material is not None and material.purchased_qty is not None:
                material.purchased_qty = to_decimal(material.purchased_qty) - qty
        db.query(ConsumableReceipt).filter_by(purchase_id=purchase_id).delete()
    db.query(ConsumablePurchaseItem).filter_by(purchase_id=purchase_id).delete()
    db.delete(row)
    db.commit()
    return row
