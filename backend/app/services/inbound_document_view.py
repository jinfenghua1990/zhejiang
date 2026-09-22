"""到仓入库单主单视图：按入库单展示真实主单、明细和采购关联。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_ as sa_and
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.models.alibaba1688_import import Alibaba1688Order
from app.models.catalog import ProductSku, Warehouse
from app.models.consumable import Consumable, ConsumableTransaction
from app.models.consumable_purchase import ConsumablePurchase, ConsumableReceipt
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.models.procurement_chain import ProcurementChainLink
from app.models.purchase import ExternalPurchaseOrder, JackyunPurchaseOrderLink
from app.models.tax import TaxInvoice, TaxInvoiceLink
from app.services.tax_invoice_service import filter_visible_invoices
from app.services.warehouse_service import display_code, resolve_warehouse_reference


def _number(value: Decimal | None) -> float:
    return float(value or 0)


def _business_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    tz = ZoneInfo(settings.TZ)
    return value.astimezone(tz) if value.tzinfo is not None else value.replace(tzinfo=tz)


def _day_start(value: date) -> datetime:
    return datetime.combine(value, time.min).replace(tzinfo=ZoneInfo(settings.TZ))


def _source(document: JackyunGoodsDocument) -> str:
    return str((document.raw or {}).get("source") or "jackyun_history")


def _status(items: list[JackyunGoodsDocumentItem]) -> str:
    if not items:
        return "异常"
    actual = sum((item.quantity or Decimal("0")) for item in items)
    arrived = sum((item.apply_quantity if item.apply_quantity is not None else item.quantity or Decimal("0")) for item in items)
    if any(item.match_status == "missing" for item in items):
        return "异常"
    if actual <= 0:
        return "待入库"
    if arrived > actual:
        return "部分入库"
    return "已入库"


def _date_text(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class _InboundPrefetch:
    """入库列表的关联数据缓存，避免每张入库单重复查明细和发票链路。"""

    def __init__(self, db: Session):
        self.items_by_document: dict[int, list[JackyunGoodsDocumentItem]] = {}
        for item in db.query(JackyunGoodsDocumentItem).order_by(JackyunGoodsDocumentItem.line_no).all():
            self.items_by_document.setdefault(item.document_id, []).append(item)

        # 匹配后的货品档案：明细展示以匹配货品为准（吉客云原录名称作对照）
        self.skus_by_id: dict[int, ProductSku] = {
            row.id: row for row in db.query(ProductSku).all()
        }

        self.links_by_document: dict[int, list[ProcurementChainLink]] = {}
        for link in db.query(ProcurementChainLink).filter(
            ProcurementChainLink.target_type == "inbound"
        ).order_by(ProcurementChainLink.id).all():
            self.links_by_document.setdefault(link.target_id, []).append(link)

        self.orders_by_id = {
            row.id: row for row in db.query(Alibaba1688Order).all()
        }
        self.external_by_id = {
            row.id: row for row in db.query(ExternalPurchaseOrder).all()
        }
        self.external_by_order_no: dict[str, list[ExternalPurchaseOrder]] = {}
        for row in sorted(self.external_by_id.values(), key=lambda value: value.id, reverse=True):
            if (row.platform or "1688") == "1688":
                self.external_by_order_no.setdefault(row.external_order_id, []).append(row)

        self.purchase_links_by_po: dict[int, list[JackyunPurchaseOrderLink]] = {}
        for link in db.query(JackyunPurchaseOrderLink).all():
            self.purchase_links_by_po.setdefault(link.po_id, []).append(link)

        self.visible_invoices = {
            invoice.id: invoice
            for invoice in filter_visible_invoices(db.query(TaxInvoice)).all()
        }
        self.invoice_links_by_target: dict[tuple[str, int], list[TaxInvoiceLink]] = {}
        for link in db.query(TaxInvoiceLink).filter(
            TaxInvoiceLink.match_method != "rejected"
        ).all():
            self.invoice_links_by_target.setdefault(
                (link.target_type, link.target_id), []
            ).append(link)

        self.warehouses_by_reference: dict[str, list[Warehouse]] = {}
        for row in db.query(Warehouse).all():
            for value in (row.jackyun_warehouse_id, row.code, row.name):
                key = str(value or "").strip().casefold()
                if key and row not in self.warehouses_by_reference.setdefault(key, []):
                    self.warehouses_by_reference[key].append(row)

    def resolve_warehouse(self, *, code: str | None = "", name: str | None = "") -> tuple[str, str]:
        source_code = str(code or "").strip()
        source_name = str(name or "").strip()
        for value in (source_code, source_name):
            bucket = self.warehouses_by_reference.get(value.casefold()) if value else None
            if bucket and len(bucket) == 1:
                row = bucket[0]
                return display_code(row), row.name.strip() or source_name or source_code
        return source_code, source_name


def _order_refs(
    db: Session,
    links: list[ProcurementChainLink],
    pf: _InboundPrefetch | None = None,
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for link in links:
        if link.order_id is not None:
            order = pf.orders_by_id.get(link.order_id) if pf else db.get(Alibaba1688Order, link.order_id)
            order_no = order.external_order_id if order else f"#{link.order_id}"
            supplier = ""
            platform = "1688"
            order_id = order.id if order else None
        elif link.external_po_id is not None:
            order = pf.external_by_id.get(link.external_po_id) if pf else db.get(ExternalPurchaseOrder, link.external_po_id)
            order_no = order.external_order_id if order else f"#{link.external_po_id}"
            supplier = order.supplier_name if order else ""
            platform = order.platform if order else "other"
            order_id = -order.id if order else None
        else:
            continue
        if order_no in seen:
            continue
        seen.add(order_no)
        refs.append({
            "orderId": order_id,
            "orderNo": order_no,
            "supplier": supplier,
            "platform": platform,
            "linkId": link.id,
            "matchMethod": link.match_method,
            "confirmed": bool(link.confirmed),
            "note": link.note or "",
        })
    return refs


def _invoice_trace(
    db: Session,
    document: JackyunGoodsDocument,
    links: list[ProcurementChainLink],
    pf: _InboundPrefetch | None = None,
) -> dict[str, Any]:
    """沿入库单已建立的采购关系读取进项发票，不按金额猜测发票。"""
    targets: set[tuple[str, int]] = set()
    external_ids: set[int] = set()
    for link in links:
        if link.match_method == "rejected":
            continue
        if link.order_id is not None:
            targets.add(("alibaba1688_order", link.order_id))
            source = pf.orders_by_id.get(link.order_id) if pf else db.get(Alibaba1688Order, link.order_id)
            if source is not None:
                if pf:
                    external = next(iter(pf.external_by_order_no.get(source.external_order_id, [])), None)
                else:
                    external = db.query(ExternalPurchaseOrder).filter_by(
                        platform="1688", external_order_id=source.external_order_id,
                    ).order_by(ExternalPurchaseOrder.id.desc()).first()
                if external is not None:
                    external_ids.add(external.id)
                    targets.add(("external_purchase_order", external.id))
        if link.external_po_id is not None:
            external_ids.add(link.external_po_id)
            targets.add(("external_purchase_order", link.external_po_id))

    # 如果入库关联的是本系统采购单，同时把该采购单已关联的吉客云采购单发票纳入追溯。
    if external_ids:
        if pf:
            po_links = [link for po_id in external_ids for link in pf.purchase_links_by_po.get(po_id, [])]
        else:
            po_links = db.query(JackyunPurchaseOrderLink).filter(
                JackyunPurchaseOrderLink.po_id.in_(external_ids)
            ).all()
        for po_link in po_links:
            targets.add(("jackyun_purchase_order", po_link.jackyun_po_id))

    raw = document.raw or {}
    raw_external_id = raw.get("externalPurchaseOrderId")
    if raw_external_id not in (None, ""):
        try:
            external_id = int(raw_external_id)
        except (TypeError, ValueError):
            external_id = None
        if external_id is not None:
            external_ids.add(external_id)
            targets.add(("external_purchase_order", external_id))

    if pf:
        visible_invoices = pf.visible_invoices
        invoice_links = [
            link
            for target in targets
            for link in pf.invoice_links_by_target.get(target, [])
            if link.invoice_id in visible_invoices
        ]
    else:
        visible_invoices = {
            invoice.id: invoice
            for invoice in filter_visible_invoices(db.query(TaxInvoice)).all()
        }
        invoice_links = [
            link for link in db.query(TaxInvoiceLink).all()
            if link.match_method != "rejected"
            and (link.target_type, link.target_id) in targets
            and link.invoice_id in visible_invoices
        ]
    grouped: dict[int, dict[str, Any]] = {}
    for link in invoice_links:
        invoice = visible_invoices[link.invoice_id]
        item = grouped.setdefault(invoice.id, {
            "invoice": invoice,
            "links": [],
            "allocatedAmount": Decimal("0"),
            "hasAllocatedAmount": False,
        })
        item["links"].append(link)
        if link.allocated_amount is not None:
            item["allocatedAmount"] += Decimal(str(link.allocated_amount))
            item["hasAllocatedAmount"] = True

    invoices: list[dict[str, Any]] = []
    received_amount = Decimal("0")
    verified_count = 0
    for item in grouped.values():
        invoice: TaxInvoice = item["invoice"]
        invoice_amount = Decimal(str(invoice.total_amount)) if invoice.total_amount is not None else None
        matched_amount = item["allocatedAmount"] if item["hasAllocatedAmount"] else invoice_amount
        if matched_amount is not None:
            received_amount += matched_amount
        if invoice.verified:
            verified_count += 1
        linked = item["links"]
        invoices.append({
            "invoiceId": invoice.id,
            "invoiceNo": f"{invoice.invoice_code or ''}{invoice.invoice_number or ''}",
            "sellerName": invoice.seller_name or "",
            "amount": float(invoice_amount) if invoice_amount is not None else None,
            "matchedAmount": float(matched_amount) if matched_amount is not None else None,
            "issueDate": invoice.issue_date.isoformat() if invoice.issue_date else None,
            "status": invoice.status or "",
            "matchStatus": "已确认" if any(link.confirmed for link in linked) else "待确认",
            "matchMethod": linked[0].match_method or "",
            "confirmed": any(link.confirmed for link in linked),
            "verified": bool(invoice.verified),
            "verifiedMonth": invoice.verified_month or "",
            "linkId": linked[0].id,
            "linkIds": [link.id for link in linked],
            "note": next((link.note for link in linked if link.note), ""),
        })

    invoices.sort(key=lambda row: (row["issueDate"] or "", row["invoiceId"]), reverse=True)
    expected_amount = Decimal(str(document.total_amount)) if document.total_amount is not None else None
    outstanding = None
    if expected_amount is not None:
        outstanding = max(expected_amount - received_amount, Decimal("0"))
    if not invoices:
        status = "待开票"
    elif outstanding is not None and outstanding > Decimal("0.01"):
        status = "部分开票"
    else:
        status = "已开票"
    return {
        "status": status,
        "expectedAmount": float(expected_amount) if expected_amount is not None else None,
        "receivedAmount": float(received_amount),
        "outstandingAmount": float(outstanding) if outstanding is not None else None,
        "invoiceCount": len(invoices),
        "verifiedCount": verified_count,
        "invoices": invoices,
    }


def _serialize(
    db: Session,
    document: JackyunGoodsDocument,
    pf: _InboundPrefetch | None = None,
) -> dict[str, Any]:
    if pf:
        items = pf.items_by_document.get(document.id, [])
        links = pf.links_by_document.get(document.id, [])
    else:
        items = (
            db.query(JackyunGoodsDocumentItem)
            .filter(JackyunGoodsDocumentItem.document_id == document.id)
            .order_by(JackyunGoodsDocumentItem.line_no)
            .all()
        )
        links = (
            db.query(ProcurementChainLink)
            .filter(
                ProcurementChainLink.target_type == "inbound",
                ProcurementChainLink.target_id == document.id,
            )
            .order_by(ProcurementChainLink.id)
            .all()
        )
    refs = _order_refs(db, links, pf)
    invoice_trace = _invoice_trace(db, document, links, pf)
    arrived = sum((item.apply_quantity if item.apply_quantity is not None else item.quantity or Decimal("0")) for item in items)
    actual = sum((item.quantity or Decimal("0")) for item in items)
    source = _source(document)
    raw = document.raw or {}
    product_names = [item.goods_name for item in items if item.goods_name]
    status = _status(items)
    if pf:
        warehouse_code, warehouse_name = pf.resolve_warehouse(
            code=document.warehouse_code, name=document.warehouse_name,
        )
    else:
        warehouse_code, warehouse_name = resolve_warehouse_reference(
            db, code=document.warehouse_code, name=document.warehouse_name,
        )
    operations = [{
        "action": "本系统创建" if source == "local_purchase_inbound" else "导入吉客云入库单",
        "at": _date_text(document.created_at),
        "note": raw.get("sourceImportId") and f"来源导入批次 #{raw['sourceImportId']}" or "",
    }]
    operations.extend({
        "action": "关联采购订单",
        "at": _date_text(link.created_at),
        "note": next((ref["orderNo"] for ref in refs if ref["linkId"] == link.id), ""),
    } for link in links)
    # 关联订单号（去重、剔除占位符），用于列表展示与快速搜索定位
    order_nos = {ref["orderNo"] for ref in refs if ref.get("orderNo") and not str(ref["orderNo"]).startswith("#")}
    if raw.get("platformPurchaseOrderNo"):
        order_nos.add(str(raw["platformPurchaseOrderNo"]))
    return {
        "id": document.id,
        "inboundNo": document.goodsdoc_no,
        "source": source,
        "isLocal": source == "local_purchase_inbound",
        "supplier": document.supplier_name or "未填写",
        "warehouse": warehouse_name or warehouse_code or "未指定",
        "warehouseCode": warehouse_code or "",
        "productCount": len(items),
        "productSummary": f"{len(product_names)} 个商品 / {_number(actual):g} 件",
        "arrivedQuantity": _number(arrived),
        "actualQuantity": _number(actual),
        "difference": _number(arrived - actual),
        "status": status,
        "inboundAt": _date_text(document.document_at),
        "createdAt": _date_text(document.created_at),
        "relatedInboundNos": [document.goodsdoc_no] if source != "local_purchase_inbound" else [],
        "platformPurchaseOrderNo": raw.get("platformPurchaseOrderNo") or "",
        "orderNos": sorted(order_nos),
        "refs": refs,
        "invoiceStatus": invoice_trace["status"],
        "invoiceExpectedAmount": invoice_trace["expectedAmount"],
        "invoiceReceivedAmount": invoice_trace["receivedAmount"],
        "invoiceOutstandingAmount": invoice_trace["outstandingAmount"],
        "invoiceCount": invoice_trace["invoiceCount"],
        "invoiceVerifiedCount": invoice_trace["verifiedCount"],
        "invoices": invoice_trace["invoices"],
        "items": [_serialize_item(db, item, pf) for item in items],
        "operations": operations,
    }


def _serialize_item(
    db: Session,
    item: JackyunGoodsDocumentItem,
    pf: _InboundPrefetch | None = None,
) -> dict[str, Any]:
    """明细行序列化：展示以匹配后的货品档案为准，保留吉客云原录名称作对照。"""
    matched = None
    if item.matched_sku_id is not None:
        matched = pf.skus_by_id.get(item.matched_sku_id) if pf else db.get(ProductSku, item.matched_sku_id)
    raw_name = item.goods_name or "未命名货品"
    raw_code = item.goods_no or item.sku_barcode or "—"
    if matched is not None:
        sku_code = matched.sku_code
        goods_name = matched.sku_name or raw_name
        raw_name = "" if (matched.sku_name or raw_name) == raw_name and matched.sku_code == raw_code else raw_name
    else:
        sku_code, goods_name = raw_code, raw_name
    return {
        "id": item.id,
        "lineNo": item.line_no,
        "sku": sku_code,
        "goodsName": goods_name,
        "rawGoodsName": raw_name,
        "spec": item.spec or "—",
        "unit": item.unit_name or "件",
        "arrivedQuantity": _number(item.apply_quantity if item.apply_quantity is not None else item.quantity),
        "actualQuantity": _number(item.quantity),
        "difference": _number((item.apply_quantity if item.apply_quantity is not None else item.quantity or Decimal("0")) - (item.quantity or Decimal("0"))),
        "unitPrice": _number(item.unit_price_tax),
        "amountTax": _number(item.amount_tax),
        "matchStatus": item.match_status or "unmatched",
        "note": item.match_note or "",
    }


def _consumable_inbound_rows(db: Session) -> list[dict[str, Any]]:
    """把耗材采购入库流水聚合成虚拟入库单行。

    采购订单工作台「登记耗材入库单」与耗材采购收货写的是 ConsumableTransaction
    （transaction_type="purchase"，不经过吉客云入库单）。分组键 =
    (source_type, source_id)：同一次收货（source_type="consumable_receipt"）
    的多条流水合成一张入库单；无来源单据的手工采购入库流水每条自成一张。
    只聚合真正计入库存的入库事实，consume/loss/stocktake 等出库与调整不进来。
    """
    transactions = (
        db.query(ConsumableTransaction)
        .filter(ConsumableTransaction.transaction_type == "purchase")
        .order_by(ConsumableTransaction.occurred_at, ConsumableTransaction.id)
        .all()
    )
    if not transactions:
        return []

    material_ids = {tx.consumable_id for tx in transactions}
    materials = {row.id: row for row in db.query(Consumable).filter(Consumable.id.in_(material_ids)).all()}
    warehouse_ids = {tx.warehouse_id for tx in transactions if tx.warehouse_id is not None}
    warehouses = {
        row.id: row for row in db.query(Warehouse).filter(Warehouse.id.in_(warehouse_ids)).all()
    } if warehouse_ids else {}

    receipt_ids = {
        tx.source_id for tx in transactions
        if tx.source_type == "consumable_receipt" and tx.source_id is not None
    }
    receipts = {
        row.id: row for row in db.query(ConsumableReceipt).filter(ConsumableReceipt.id.in_(receipt_ids)).all()
    } if receipt_ids else {}
    purchase_ids = {receipt.purchase_id for receipt in receipts.values()}
    purchases = {
        row.id: row for row in db.query(ConsumablePurchase).filter(ConsumablePurchase.id.in_(purchase_ids)).all()
    } if purchase_ids else {}
    order_ids = {purchase.source_order_id for purchase in purchases.values() if purchase.source_order_id}
    orders = {
        row.id: row for row in db.query(Alibaba1688Order).filter(Alibaba1688Order.id.in_(order_ids)).all()
    } if order_ids else {}

    groups: dict[tuple[str, int | None], list[ConsumableTransaction]] = {}
    for tx in transactions:
        # 删除耗材采购单时，原 purchase 流水按审计要求保留，并通过反向流水冲销。
        # 这类流水的 consumable_receipt 已不存在，不能继续在“到仓入库单”里显示成有效入库。
        if (
            tx.source_type == "consumable_receipt"
            and tx.source_id is not None
            and tx.source_id not in receipts
        ):
            continue
        groups.setdefault((tx.source_type or "manual", tx.source_id), []).append(tx)

    rows: list[dict[str, Any]] = []
    for (source_type, source_id), group in groups.items():
        head = group[0]
        receipt = receipts.get(source_id) if source_type == "consumable_receipt" and source_id is not None else None
        purchase = purchases.get(receipt.purchase_id) if receipt is not None else None
        order = orders.get(purchase.source_order_id) if purchase is not None and purchase.source_order_id else None

        items = []
        for line_no, tx in enumerate(group, start=1):
            material = materials.get(tx.consumable_id)
            quantity = _number(tx.quantity)
            items.append({
                "id": -tx.id,
                "lineNo": line_no,
                "sku": (material.code if material else "") or f"耗材#{tx.consumable_id}",
                "goodsName": (material.name if material else "") or "未命名耗材",
                "spec": "—",
                "unit": (material.unit if material else "") or "件",
                "arrivedQuantity": quantity,
                "actualQuantity": quantity,
                "difference": 0.0,
                "matchStatus": "matched",
                "note": tx.note or "",
            })

        refs = []
        if order is not None and purchase is not None:
            refs.append({
                "orderId": order.id,
                "orderNo": order.external_order_id,
                "supplier": purchase.supplier_name,
                "platform": "1688",
                "linkId": -head.id,
                "matchMethod": "consumable_receipt",
                "confirmed": True,
                "note": f"耗材采购单 {purchase.number}",
            })

        occurred = [_business_time(tx.occurred_at) for tx in group if tx.occurred_at]
        inbound_dt = min(occurred) if occurred else None
        created = [_business_time(tx.created_at) for tx in group if tx.created_at]
        warehouse = warehouses.get(head.warehouse_id)
        total_quantity = _number(sum((tx.quantity or Decimal("0")) for tx in group))
        operation_note = (
            f"{purchase.number} / {receipt.number} {receipt.note}".strip()
            if receipt is not None and purchase is not None
            else (receipt.number if receipt is not None else head.note or "")
        )
        operations = [{
            "action": "登记耗材入库",
            "at": inbound_dt.isoformat() if inbound_dt else None,
            "note": operation_note,
        }]
        if purchase is not None:
            operations.append({
                "action": "关联耗材采购单",
                "at": _date_text(purchase.created_at),
                "note": f"{purchase.number} · {purchase.supplier_name}",
            })

        rows.append({
            "id": -head.id,
            "inboundNo": receipt.number if receipt is not None else f"HC入库-{head.id}",
            "source": "consumable",
            "isLocal": True,
            "supplier": (purchase.supplier_name if purchase else "") or "未填写",
            "warehouse": (warehouse.name if warehouse else "") or "未指定",
            "warehouseCode": (warehouse.code if warehouse else "") or "",
            "productCount": len(items),
            "productSummary": f"{len(items)} 个耗材 / {total_quantity:g} 件",
            "arrivedQuantity": total_quantity,
            "actualQuantity": total_quantity,
            "difference": 0.0,
            "status": "已入库",
            "inboundAt": inbound_dt.isoformat() if inbound_dt else None,
            "createdAt": _date_text(min(created)) if created else None,
            "relatedInboundNos": [],
            "platformPurchaseOrderNo": order.external_order_id if order else "",
            "orderNos": [ref["orderNo"] for ref in refs if ref.get("orderNo") and not str(ref["orderNo"]).startswith("#")],
            "refs": refs,
            "invoiceStatus": "待开票",
            "invoiceExpectedAmount": None,
            "invoiceReceivedAmount": 0.0,
            "invoiceOutstandingAmount": None,
            "invoiceCount": 0,
            "invoiceVerifiedCount": 0,
            "invoices": [],
            "items": items,
            "operations": operations,
            # 内部排序/日期过滤用的临时字段，返回前移除。
            "_inbound_dt": inbound_dt,
        })
    return rows


def _consumable_row_matches(row: dict[str, Any], pattern: str) -> bool:
    haystacks = [row["inboundNo"], row["supplier"], row["warehouse"], row["warehouseCode"]]
    haystacks.extend(row.get("orderNos") or [])
    for item in row["items"]:
        haystacks.extend([item.get("sku") or "", item.get("goodsName") or "", item.get("note") or ""])
    return any(pattern in str(value or "").lower() for value in haystacks)


def list_inbound_documents(
    db: Session,
    *,
    q: str = "",
    status: str = "",
    warehouse: str = "",
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    query = db.query(JackyunGoodsDocument).filter(JackyunGoodsDocument.document_type == "inbound")
    term = q.strip()
    if term:
        like = f"%{term}%"
        # 订单号搜索：沿入库单的采购关联（1688 原件 / 本系统采购单）匹配订单号
        query = (
            query.outerjoin(JackyunGoodsDocumentItem, JackyunGoodsDocumentItem.document_id == JackyunGoodsDocument.id)
            .outerjoin(
                ProcurementChainLink,
                sa_and(
                    ProcurementChainLink.target_type == "inbound",
                    ProcurementChainLink.target_id == JackyunGoodsDocument.id,
                ),
            )
            .outerjoin(Alibaba1688Order, Alibaba1688Order.id == ProcurementChainLink.order_id)
            .outerjoin(ExternalPurchaseOrder, ExternalPurchaseOrder.id == ProcurementChainLink.external_po_id)
            .filter(
                JackyunGoodsDocument.goodsdoc_no.ilike(like)
                | JackyunGoodsDocument.supplier_name.ilike(like)
                | JackyunGoodsDocument.warehouse_name.ilike(like)
                | JackyunGoodsDocumentItem.goods_no.ilike(like)
                | JackyunGoodsDocumentItem.sku_barcode.ilike(like)
                | JackyunGoodsDocumentItem.goods_name.ilike(like)
                | Alibaba1688Order.external_order_id.ilike(like)
                | ExternalPurchaseOrder.external_order_id.ilike(like)
            )
            .distinct()
        )
    if warehouse:
        normalized_code, normalized_name = resolve_warehouse_reference(
            db, code=warehouse, name=warehouse,
        )
        values = {value for value in (warehouse.strip(), normalized_code, normalized_name) if value}
        query = query.filter(
            or_(
                JackyunGoodsDocument.warehouse_name.in_(values),
                JackyunGoodsDocument.warehouse_code.in_(values),
            )
        )
    if start_date:
        query = query.filter(JackyunGoodsDocument.document_at >= _day_start(start_date))
    if end_date:
        query = query.filter(JackyunGoodsDocument.document_at < _day_start(end_date + timedelta(days=1)))

    documents = query.order_by(JackyunGoodsDocument.document_at.desc().nullslast(), JackyunGoodsDocument.id.desc()).all()
    prefetch = _InboundPrefetch(db)
    rows = [_serialize(db, document, prefetch) for document in documents]

    # 耗材采购入库流水合成的虚拟入库单，与吉客云入库单按时间倒序混排。
    consumable_rows = _consumable_inbound_rows(db)
    if term:
        pattern = term.lower()
        consumable_rows = [row for row in consumable_rows if _consumable_row_matches(row, pattern)]
    warehouse_values: set[str] | None = None
    if warehouse:
        normalized_code, normalized_name = resolve_warehouse_reference(
            db, code=warehouse, name=warehouse,
        )
        warehouse_values = {value for value in (warehouse.strip(), normalized_code, normalized_name) if value}
    lower_bound = _day_start(start_date) if start_date else None
    upper_bound = _day_start(end_date + timedelta(days=1)) if end_date else None
    filtered: list[dict[str, Any]] = []
    for row in consumable_rows:
        if warehouse_values is not None and row["warehouse"] not in warehouse_values and row["warehouseCode"] not in warehouse_values:
            continue
        inbound_dt = row.get("_inbound_dt")
        if lower_bound is not None and (inbound_dt is None or inbound_dt < lower_bound):
            continue
        if upper_bound is not None and (inbound_dt is None or inbound_dt >= upper_bound):
            continue
        filtered.append(row)
    rows.extend(filtered)

    rows.sort(key=lambda row: (row["inboundAt"] or "", row["id"]), reverse=True)
    for row in rows:
        row.pop("_inbound_dt", None)
    if status:
        rows = [row for row in rows if row["status"] == status]
    total = len(rows)
    stats = {
        "all": total,
        "pending": sum(row["status"] in {"待入库", "部分入库"} for row in rows),
        "done": sum(row["status"] == "已入库" for row in rows),
        "exception": sum(row["status"] == "异常" for row in rows),
    }
    warehouses = sorted({row["warehouse"] for row in rows})
    return {
        "total": total,
        "stats": stats,
        "warehouses": warehouses,
        "rows": rows[offset:offset + min(max(limit, 1), 500)],
    }
