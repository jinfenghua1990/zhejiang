"""业务事实 → 统一财务事项池。

原则：
- 业务表仍是真实来源，FinanceEntry 是可重建的财务投影。
- 同一来源/类别幂等更新，不重复插入。
- 外贸进口费用只有 importer_kind=own_entity 且绑定我方主体时才进公司账。
- 可抵扣进口 VAT 影响现金但不影响利润。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance import FinanceEntry
from app.models.foreign_trade import ForeignTradeOrder, ForeignTradeShipment
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.models.sales import AftersalesOrder, SalesOrder, SalesOrderItem
from app.services import finance_center_service, foreign_trade_service
from app.services.inbound_cost_service import resolve_sales_sku_id, sales_sku_lookup, weighted_inbound_costs
from app.services.monthly_core import month_bounds
from app.services.sales_scope import deal_orders_condition


@dataclass(frozen=True)
class EntrySpec:
    legal_entity_id: int
    business_scope: str
    source_type: str
    source_id: str
    source_no: str
    category: str
    direction: str
    currency: str
    amount: Decimal
    value_type: str
    settlement_status: str
    invoice_status: str
    occurred_at: datetime | None
    cash_effect: bool = True
    profit_effect: bool = True
    tax_amount: Decimal = Decimal("0")
    note: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def _money(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def _event(row: Any, *names: str) -> datetime:
    for name in names:
        value = getattr(row, name, None)
        if isinstance(value, datetime):
            return value
    created = getattr(row, "created_at", None)
    if isinstance(created, datetime):
        return created
    return datetime.now(timezone.utc)


def _period(value: datetime | None) -> tuple[int, int]:
    event = value or datetime.now(timezone.utc)
    business_tz = ZoneInfo(settings.TZ)
    if event.tzinfo is None:
        event = event.replace(tzinfo=business_tz)
    else:
        event = event.astimezone(business_tz)
    return event.year, event.month


def _upsert_spec(db: Session, spec: EntrySpec) -> tuple[FinanceEntry, bool]:
    row = db.scalar(
        select(FinanceEntry).where(
            FinanceEntry.legal_entity_id == spec.legal_entity_id,
            FinanceEntry.source_type == spec.source_type,
            FinanceEntry.source_id == spec.source_id,
            FinanceEntry.category == spec.category,
            FinanceEntry.value_type == spec.value_type,
        )
    )
    created = row is None
    if row is None:
        row = FinanceEntry(
            legal_entity_id=spec.legal_entity_id,
            source_type=spec.source_type,
            source_id=spec.source_id,
            category=spec.category,
            value_type=spec.value_type,
        )

    year, month = _period(spec.occurred_at)
    row.business_scope = spec.business_scope
    row.source_no = spec.source_no
    row.direction = spec.direction
    row.cash_effect = spec.cash_effect
    row.profit_effect = spec.profit_effect
    row.currency = spec.currency.upper()
    row.amount = spec.amount
    row.tax_amount = spec.tax_amount
    row.settlement_status = spec.settlement_status
    row.invoice_status = spec.invoice_status
    row.accounting_year = year
    row.accounting_month = month
    row.occurred_at = spec.occurred_at
    row.note = spec.note
    row.raw = {**(row.raw or {}), **spec.raw, "projection": True}
    db.add(row)
    return row, created


def _reconcile_source(
    db: Session,
    *,
    source_type: str,
    source_id: str,
    specs: list[EntrySpec],
) -> dict[str, int]:
    expected = {
        (spec.legal_entity_id, spec.category, spec.value_type)
        for spec in specs
        if spec.amount != 0
    }
    existing = db.scalars(
        select(FinanceEntry).where(
            FinanceEntry.source_type == source_type,
            FinanceEntry.source_id == source_id,
        )
    ).all()
    deleted = 0
    for row in existing:
        key = (row.legal_entity_id, row.category, row.value_type)
        if key not in expected and (row.raw or {}).get("projection"):
            db.delete(row)
            deleted += 1

    created = 0
    updated = 0
    for spec in specs:
        if spec.amount == 0:
            continue
        _, was_created = _upsert_spec(db, spec)
        if was_created:
            created += 1
        else:
            updated += 1
    return {"created": created, "updated": updated, "deleted": deleted}


def delete_projected_source(db: Session, source_type: str, source_id: str) -> int:
    rows = db.scalars(
        select(FinanceEntry).where(
            FinanceEntry.source_type == source_type,
            FinanceEntry.source_id == source_id,
        )
    ).all()
    deleted = 0
    for row in rows:
        if (row.raw or {}).get("projection"):
            db.delete(row)
            deleted += 1
    return deleted


def project_domestic_sales_order(db: Session, row: SalesOrder) -> dict[str, int]:
    entity = finance_center_service.resolve_entity(db)
    amount = _money(row.paid_amount)
    event = row.paid_at or row.ordered_at or _event(row)
    specs = [
        EntrySpec(
            legal_entity_id=entity.id,
            business_scope="domestic",
            source_type="domestic_sales_order",
            source_id=str(row.id),
            source_no=row.order_no,
            category="sales_income",
            direction="income",
            currency=row.currency or entity.base_currency,
            amount=amount,
            value_type="actual",
            settlement_status="settled" if amount > 0 else "pending",
            invoice_status="unknown",
            occurred_at=event,
            cash_effect=True,
            profit_effect=True,
            note=f"内销订单实付收入 · {row.platform or row.source_provider}",
            raw={"platform": row.platform, "provider": row.source_provider},
        )
    ]

    # 销售成本只在该订单所有可核算 SKU 都有入库成本时写入，避免用“部分成本”
    # 造成利润被虚高。入库成本后续补齐后再次同步会自动生成/更新该事项。
    items = db.scalars(
        select(SalesOrderItem).where(SalesOrderItem.order_id == row.id)
    ).all()
    if items:
        lookup = sales_sku_lookup(db)
        resolved: list[tuple[SalesOrderItem, int | None]] = [
            (item, resolve_sales_sku_id(item.sku_id, item.sku_code, lookup))
            for item in items
        ]
        sku_ids = {sku_id for _, sku_id in resolved if sku_id is not None}
        _, month = _period(event)
        year, _ = _period(event)
        _, period_end = month_bounds(year, month)
        costs = weighted_inbound_costs(db, as_of=period_end, sku_ids=sku_ids)
        missing = [
            item.sku_code or item.goods_name or f"line-{item.id}"
            for item, sku_id in resolved
            if sku_id is None or sku_id not in costs
        ]
        if not missing:
            cost_total = sum(
                (_money(item.quantity) * costs[int(sku_id)] for item, sku_id in resolved if sku_id is not None),
                Decimal("0"),
            )
            specs.append(EntrySpec(
                legal_entity_id=entity.id,
                business_scope="domestic",
                source_type="domestic_sales_order",
                source_id=str(row.id),
                source_no=row.order_no,
                category="sales_cost",
                direction="expense",
                currency="CNY",
                amount=cost_total,
                value_type="actual",
                settlement_status="closed",
                invoice_status="not_required",
                occurred_at=event,
                cash_effect=False,
                profit_effect=True,
                note="销售成本 · 按账期截止前采购入库加权成本",
                raw={"costComplete": True, "itemCount": len(items)},
            ))

    return _reconcile_source(
        db,
        source_type="domestic_sales_order",
        source_id=str(row.id),
        specs=specs,
    )


def project_domestic_refund(db: Session, row: AftersalesOrder) -> dict[str, int]:
    entity = finance_center_service.resolve_entity(db)
    amount = _money(row.refund_amount)
    event = row.created_at_src or _event(row)
    specs = [
        EntrySpec(
            legal_entity_id=entity.id,
            business_scope="domestic",
            source_type="domestic_aftersales",
            source_id=str(row.id),
            source_no=row.aftersale_no,
            category="refund",
            direction="expense",
            currency="CNY",
            amount=amount,
            value_type="actual",
            settlement_status="settled" if amount > 0 else "pending",
            invoice_status="not_required",
            occurred_at=event,
            cash_effect=True,
            profit_effect=True,
            note=f"售后退款 · 原订单 {row.order_no}",
            raw={"orderNo": row.order_no, "type": row.type, "status": row.status},
        )
    ]
    return _reconcile_source(
        db,
        source_type="domestic_aftersales",
        source_id=str(row.id),
        specs=specs,
    )


def project_inbound_document(db: Session, row: JackyunGoodsDocument) -> dict[str, int]:
    """采购入库作为库存采购/应付事实进入财务事项池，但不直接影响利润或现金。"""
    if row.document_type != "inbound":
        return {"created": 0, "updated": 0, "deleted": 0}

    entity = finance_center_service.resolve_entity(db)
    amount = _money(row.total_amount)
    if amount <= 0:
        items = db.scalars(
            select(JackyunGoodsDocumentItem).where(
                JackyunGoodsDocumentItem.document_id == row.id
            )
        ).all()
        amount = sum(
            (
                _money(item.amount_tax)
                if item.amount_tax is not None
                else _money(item.quantity) * _money(item.unit_price_tax)
                for item in items
            ),
            Decimal("0"),
        )

    event = row.document_at or _event(row)
    specs = [
        EntrySpec(
            legal_entity_id=entity.id,
            business_scope="domestic",
            source_type="domestic_inbound",
            source_id=str(row.id),
            source_no=row.goodsdoc_no,
            category="inventory_purchase",
            direction="expense",
            currency="CNY",
            amount=amount,
            value_type="actual",
            settlement_status="pending",
            invoice_status="pending",
            occurred_at=event,
            cash_effect=False,
            profit_effect=False,
            note=f"采购入库 / 应付事实 · {row.supplier_name or '供应商未填写'}",
            raw={
                "supplierName": row.supplier_name,
                "warehouseName": row.warehouse_name,
                "source": (row.raw or {}).get("source", ""),
            },
        )
    ]
    return _reconcile_source(
        db,
        source_type="domestic_inbound",
        source_id=str(row.id),
        specs=specs,
    )


def project_domestic_logistics_period(
    db: Session, *, year: int, month: int
) -> dict[str, int]:
    from app.services import logistics_service

    entity = finance_center_service.resolve_entity(db)
    payload = logistics_service.monthly_finance_cost(db, year, month)
    amount = _money(payload.get("amount"))
    event = datetime(year, month, 1, tzinfo=ZoneInfo(settings.TZ))
    specs = [
        EntrySpec(
            legal_entity_id=entity.id,
            business_scope="domestic",
            source_type="domestic_logistics_period",
            source_id=f"{year:04d}-{month:02d}",
            source_no=f"{year:04d}-{month:02d}",
            category="domestic_logistics",
            direction="expense",
            currency="CNY",
            amount=amount,
            value_type=str(payload.get("valueType") or "estimated"),
            settlement_status="settled" if payload.get("valueType") == "actual" else "pending",
            invoice_status="unknown",
            occurred_at=event,
            cash_effect=False,
            profit_effect=True,
            note=(
                "物流实际账单成本"
                if payload.get("valueType") == "actual"
                else "物流预估成本"
            ),
            raw=payload,
        )
    ]
    return _reconcile_source(
        db,
        source_type="domestic_logistics_period",
        source_id=f"{year:04d}-{month:02d}",
        specs=specs,
    )


def project_foreign_order(db: Session, row: ForeignTradeOrder) -> dict[str, int]:
    entity = finance_center_service.resolve_entity(db, row.seller_legal_entity_id)
    if row.seller_legal_entity_id is None:
        row.seller_legal_entity_id = entity.id
    source_id = str(row.id)
    event = row.paid_at or row.ordered_at or _event(row)
    specs: list[EntrySpec] = []

    paid = _money(row.paid_amount)
    if paid:
        specs.append(EntrySpec(
            legal_entity_id=entity.id,
            business_scope="foreign_trade",
            source_type="foreign_order",
            source_id=source_id,
            source_no=row.external_order_no,
            category="sales_income",
            direction="income",
            currency=row.currency or entity.base_currency,
            amount=paid,
            value_type="actual",
            settlement_status="settled",
            invoice_status="unknown",
            occurred_at=event,
            cash_effect=True,
            profit_effect=True,
            note=f"外贸订单实收 · {row.channel_code}",
            raw={"channelCode": row.channel_code, "businessMode": row.business_mode},
        ))

    refund = _money(row.refund_amount)
    if refund:
        specs.append(EntrySpec(
            legal_entity_id=entity.id,
            business_scope="foreign_trade",
            source_type="foreign_order",
            source_id=source_id,
            source_no=row.external_order_no,
            category="refund",
            direction="expense",
            currency=row.currency or entity.base_currency,
            amount=refund,
            value_type="actual",
            settlement_status="settled",
            invoice_status="not_required",
            occurred_at=event,
            cash_effect=True,
            profit_effect=True,
            note="外贸订单退款",
        ))

    payment_fee = _money(row.payment_fee)
    if payment_fee:
        specs.append(EntrySpec(
            legal_entity_id=entity.id,
            business_scope="foreign_trade",
            source_type="foreign_order",
            source_id=source_id,
            source_no=row.external_order_no,
            category="platform_fee",
            direction="expense",
            currency=row.currency or entity.base_currency,
            amount=payment_fee,
            value_type="actual",
            settlement_status="settled",
            invoice_status="unknown",
            occurred_at=event,
            cash_effect=True,
            profit_effect=True,
            note="海外渠道 / 支付手续费",
        ))

    return _reconcile_source(
        db,
        source_type="foreign_order",
        source_id=source_id,
        specs=specs,
    )


def _shipment_actual(row: ForeignTradeShipment) -> bool:
    return bool(
        row.departed_at
        or row.status in {
            "departed", "in_transit", "arrived_eu", "import_customs",
            "customs_cleared", "last_mile", "delivered",
        }
    )


def _import_actual(row: ForeignTradeShipment) -> bool:
    return bool(row.customs_cleared_at or row.status in {"customs_cleared", "last_mile", "delivered"})


def project_foreign_shipment(db: Session, row: ForeignTradeShipment) -> dict[str, int]:
    exporter = finance_center_service.resolve_entity(db, row.exporter_legal_entity_id)
    if row.exporter_legal_entity_id is None:
        row.exporter_legal_entity_id = exporter.id

    source_id = str(row.id)
    export_event = _event(row, "departed_at", "etd")
    import_event = _event(row, "customs_cleared_at", "arrived_eu_at", "eta", "etd")
    export_actual = _shipment_actual(row)
    import_actual = _import_actual(row)
    specs: list[EntrySpec] = []

    def add_export(
        category: str,
        amount: Decimal,
        currency: str,
        *,
        profit_effect: bool = True,
        cash_effect: bool = True,
        note: str,
    ) -> None:
        if amount == 0:
            return
        specs.append(EntrySpec(
            legal_entity_id=exporter.id,
            business_scope="foreign_trade",
            source_type="foreign_shipment",
            source_id=source_id,
            source_no=row.shipment_no,
            category=category,
            direction="expense",
            currency=currency,
            amount=amount,
            value_type="actual" if export_actual else "estimated",
            settlement_status="pending",
            invoice_status="pending" if cash_effect else "unknown",
            occurred_at=export_event,
            cash_effect=cash_effect,
            profit_effect=profit_effect,
            note=note,
            raw={"shipmentStatus": row.status},
        ))

    # 出运货品成本影响利润，但采购付款由采购/银行模块处理，不能重复进入现金流。
    add_export(
        "shipment_goods_cost",
        _money(row.export_purchase_cost_cny),
        "CNY",
        cash_effect=False,
        note="Shipment 分摊货品成本",
    )
    add_export(
        "export_fee",
        _money(row.domestic_export_cost_cny),
        "CNY",
        note="国内出口 / 报关 / 提货等费用",
    )
    add_export(
        "international_freight",
        _money(row.freight_to_eu),
        row.currency or "EUR",
        note="国际干线运费",
    )
    add_export(
        "cargo_insurance",
        _money(row.insurance),
        row.currency or "EUR",
        note="国际货运保险",
    )

    estimated_refund = (
        _money(row.export_refund_base_cny) * _money(row.export_refund_rate) / Decimal("100")
    )
    actual_refund = _money(row.actual_export_refund_cny)
    if actual_refund > 0:
        refund_event = row.export_refund_received_at or _event(row, "updated_at", "departed_at", "etd")
        specs.append(EntrySpec(
            legal_entity_id=exporter.id,
            business_scope="foreign_trade",
            source_type="foreign_shipment",
            source_id=source_id,
            source_no=row.shipment_no,
            category="export_tax_refund",
            direction="income",
            currency="CNY",
            amount=actual_refund,
            value_type="actual",
            settlement_status="settled",
            invoice_status="not_required",
            occurred_at=refund_event,
            cash_effect=True,
            profit_effect=True,
            note="出口退税实际到账",
            raw={"estimatedAmount": str(estimated_refund), "refundStatus": row.export_refund_status},
        ))
    elif estimated_refund > 0:
        specs.append(EntrySpec(
            legal_entity_id=exporter.id,
            business_scope="foreign_trade",
            source_type="foreign_shipment",
            source_id=source_id,
            source_no=row.shipment_no,
            category="export_tax_refund",
            direction="income",
            currency="CNY",
            amount=estimated_refund,
            value_type="estimated",
            settlement_status="pending",
            invoice_status="not_required",
            occurred_at=export_event,
            cash_effect=True,
            profit_effect=True,
            note="出口退税预计",
            raw={"refundStatus": row.export_refund_status},
        ))

    # 进口侧只有“我方公司主体作为 importer”才进入公司财务事项。
    importer = None
    if row.importer_kind == "own_entity" and row.importer_legal_entity_id is not None:
        importer = finance_center_service.resolve_entity(db, row.importer_legal_entity_id)

    if importer is not None:
        costs = foreign_trade_service.shipment_costs(row)
        import_value_type = "actual" if import_actual else "estimated"
        common = dict(
            legal_entity_id=importer.id,
            business_scope="foreign_trade",
            source_type="foreign_shipment",
            source_id=source_id,
            source_no=row.shipment_no,
            direction="expense",
            currency=row.currency or importer.base_currency,
            value_type=import_value_type,
            settlement_status="pending",
            occurred_at=import_event,
            cash_effect=True,
        )
        import_specs = [
            ("customs_duty", _money(costs["customsDuty"]), True, "普通进口关税"),
            ("anti_dumping_duty", _money(costs["antiDumpingDuty"]), True, "反倾销税"),
            ("countervailing_duty", _money(costs["countervailingDuty"]), True, "反补贴税"),
            (
                "import_vat",
                _money(costs["importVat"]),
                not bool(row.import_vat_recoverable),
                "进口 VAT（可抵扣时仅影响现金，不影响利润）",
            ),
            ("clearance_fee", _money(row.clearance_fee), True, "进口清关费"),
            ("port_fee", _money(row.port_fee), True, "港杂 / 码头费"),
            ("last_mile_fee", _money(row.last_mile_fee), True, "海外末端配送"),
            ("other", _money(row.other_import_fee), True, "其他进口费用"),
        ]
        for category, amount, profit_effect, note in import_specs:
            if amount == 0:
                continue
            specs.append(EntrySpec(
                **common,
                category=category,
                amount=amount,
                profit_effect=profit_effect,
                invoice_status="not_required" if category in {
                    "customs_duty", "anti_dumping_duty", "countervailing_duty", "import_vat"
                } else "pending",
                note=note,
                raw={
                    "importerKind": row.importer_kind,
                    "importVatRecoverable": bool(row.import_vat_recoverable),
                    "shipmentStatus": row.status,
                },
            ))

    return _reconcile_source(
        db,
        source_type="foreign_shipment",
        source_id=source_id,
        specs=specs,
    )


def sync_business_period(
    db: Session,
    *,
    year: int,
    month: int,
    business_scope: str = "all",
) -> dict[str, Any]:
    start, end = month_bounds(year, month)
    totals = {"created": 0, "updated": 0, "deleted": 0}
    sources = {
        "domesticOrders": 0,
        "domesticRefunds": 0,
        "inboundDocuments": 0,
        "logisticsPeriods": 0,
        "foreignOrders": 0,
        "shipments": 0,
    }

    def merge(result: dict[str, int]) -> None:
        for key in totals:
            totals[key] += int(result.get(key, 0))

    if business_scope in {"all", "domestic"}:
        orders = db.scalars(
            select(SalesOrder).where(
                SalesOrder.ordered_at >= start,
                SalesOrder.ordered_at < end,
                deal_orders_condition(),
            )
        ).all()
        for row in orders:
            merge(project_domestic_sales_order(db, row))
        sources["domesticOrders"] = len(orders)

        refunds = db.scalars(
            select(AftersalesOrder).where(
                AftersalesOrder.type == "refund",
                AftersalesOrder.created_at_src >= start,
                AftersalesOrder.created_at_src < end,
            )
        ).all()
        for row in refunds:
            merge(project_domestic_refund(db, row))
        sources["domesticRefunds"] = len(refunds)

        inbound_documents = db.scalars(
            select(JackyunGoodsDocument).where(
                JackyunGoodsDocument.document_type == "inbound",
                or_(
                    and_(
                        JackyunGoodsDocument.document_at >= start,
                        JackyunGoodsDocument.document_at < end,
                    ),
                    and_(
                        JackyunGoodsDocument.document_at.is_(None),
                        JackyunGoodsDocument.created_at >= start,
                        JackyunGoodsDocument.created_at < end,
                    ),
                ),
            )
        ).all()
        for row in inbound_documents:
            merge(project_inbound_document(db, row))
        sources["inboundDocuments"] = len(inbound_documents)
        merge(project_domestic_logistics_period(db, year=year, month=month))
        sources["logisticsPeriods"] = 1

    if business_scope in {"all", "foreign_trade"}:
        foreign_orders = db.scalars(
            select(ForeignTradeOrder).where(
                or_(
                    and_(ForeignTradeOrder.paid_at >= start, ForeignTradeOrder.paid_at < end),
                    and_(
                        ForeignTradeOrder.paid_at.is_(None),
                        ForeignTradeOrder.ordered_at >= start,
                        ForeignTradeOrder.ordered_at < end,
                    ),
                )
            )
        ).all()
        for row in foreign_orders:
            merge(project_foreign_order(db, row))
        sources["foreignOrders"] = len(foreign_orders)

        shipments = db.scalars(
            select(ForeignTradeShipment).where(
                or_(
                    and_(ForeignTradeShipment.etd >= start, ForeignTradeShipment.etd < end),
                    and_(
                        ForeignTradeShipment.etd.is_(None),
                        ForeignTradeShipment.created_at >= start,
                        ForeignTradeShipment.created_at < end,
                    ),
                    and_(
                        ForeignTradeShipment.export_refund_received_at >= start,
                        ForeignTradeShipment.export_refund_received_at < end,
                    ),
                )
            )
        ).all()
        for row in shipments:
            merge(project_foreign_shipment(db, row))
        sources["shipments"] = len(shipments)

    db.commit()
    return {
        "year": year,
        "month": month,
        "businessScope": business_scope,
        "sources": sources,
        **totals,
    }
