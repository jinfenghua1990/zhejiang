"""外贸工作台业务服务。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.catalog import ProductSku
from app.models.foreign_trade import (
    ForeignTradeChannel,
    ForeignTradeDealer,
    ForeignTradeInventoryReservation,
    ForeignTradeOrder,
    ForeignTradeProduct,
    ForeignTradeProductPlatform,
    ForeignTradeShipment,
    ForeignTradeSkuMapping,
)
from app.services import inventory_position_service


def _decimal(value) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def order_profit(order: ForeignTradeOrder) -> Decimal:
    return (
        _decimal(order.paid_amount)
        - _decimal(order.refund_amount)
        - _decimal(order.payment_fee)
        - _decimal(order.purchase_cost)
        - _decimal(order.logistics_cost)
    )




def product_platform_dict(row: ForeignTradeProductPlatform) -> dict:
    return {
        "id": row.id,
        "brand": row.brand,
        "code": row.code,
        "name": row.name,
        "nameEn": row.name_en,
        "description": row.description,
        "displayOrder": row.display_order,
        "enabled": bool(row.enabled),
    }


def foreign_product_dict(
    row: ForeignTradeProduct,
    platform: ForeignTradeProductPlatform | None = None,
    sku: ProductSku | None = None,
) -> dict:
    return {
        "id": row.id,
        "brand": row.brand,
        "platformCode": row.platform_code,
        "platformName": platform.name if platform else row.platform_code,
        "platformNameEn": platform.name_en if platform else "",
        "modelCode": row.model_code,
        "name": row.name,
        "nameEn": row.name_en,
        "skuId": row.sku_id,
        "skuCode": sku.sku_code if sku else "",
        "skuName": sku.sku_name if sku else "",
        "externalSku": row.external_sku,
        "status": row.status,
        "countries": row.countries or [],
        "currency": row.currency,
        "note": row.note,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def order_dict(order: ForeignTradeOrder, dealer: ForeignTradeDealer | None = None) -> dict:
    profit = order_profit(order)
    rate = _decimal(order.exchange_rate_to_cny) or Decimal("1")
    return {
        "id": order.id,
        "channelCode": order.channel_code,
        "externalOrderNo": order.external_order_no,
        "businessMode": order.business_mode,
        "dealerId": order.dealer_id,
        "dealerName": dealer.company_name if dealer else "",
        "brand": order.brand,
        "country": order.country,
        "currency": order.currency,
        "grossAmount": str(order.gross_amount or 0),
        "discountAmount": str(order.discount_amount or 0),
        "shippingIncome": str(order.shipping_income or 0),
        "paidAmount": str(order.paid_amount or 0),
        "refundAmount": str(order.refund_amount or 0),
        "paymentFee": str(order.payment_fee or 0),
        "purchaseCost": str(order.purchase_cost or 0),
        "logisticsCost": str(order.logistics_cost or 0),
        "exchangeRateToCny": str(order.exchange_rate_to_cny or 1),
        "profit": str(profit),
        "profitCny": str(profit * rate),
        "status": order.status,
        "procurementStatus": order.procurement_status,
        "fulfillmentStatus": order.fulfillment_status,
        "paymentStatus": order.payment_status,
        "sellerLegalEntityId": order.seller_legal_entity_id,
        "customerName": order.customer_name,
        "customerEmail": order.customer_email,
        "shipTo": order.ship_to,
        "carrier": order.carrier,
        "trackingNo": order.tracking_no,
        "orderedAt": order.ordered_at.isoformat() if order.ordered_at else None,
        "paidAt": order.paid_at.isoformat() if order.paid_at else None,
        "shippedAt": order.shipped_at.isoformat() if order.shipped_at else None,
        "items": order.items or [],
        "note": order.note,
        "createdAt": order.created_at.isoformat() if order.created_at else None,
        "updatedAt": order.updated_at.isoformat() if order.updated_at else None,
    }


def channel_dict(channel: ForeignTradeChannel) -> dict:
    return {
        "id": channel.id,
        "code": channel.code,
        "name": channel.name,
        "channelType": channel.channel_type,
        "brand": channel.brand,
        "currency": channel.currency,
        "countries": channel.countries or [],
        "enabled": bool(channel.enabled),
        "connected": bool(channel.connected),
        "note": channel.note,
    }


def sku_dict(row: ForeignTradeSkuMapping) -> dict:
    return {
        "id": row.id,
        "channelCode": row.channel_code,
        "externalSku": row.external_sku,
        "internalSku": row.internal_sku,
        "productName": row.product_name,
        "status": row.status,
        "note": row.note,
    }


def list_orders(
    db: Session,
    q: str = "",
    status: str = "",
    channel_code: str = "",
    brand: str = "",
    business_mode: str = "",
) -> list[dict]:
    stmt = select(ForeignTradeOrder).order_by(ForeignTradeOrder.id.desc()).limit(1000)
    if status:
        stmt = stmt.where(ForeignTradeOrder.status == status)
    if channel_code:
        stmt = stmt.where(ForeignTradeOrder.channel_code == channel_code)
    if brand:
        stmt = stmt.where(func.lower(ForeignTradeOrder.brand) == brand.strip().lower())
    if business_mode:
        stmt = stmt.where(ForeignTradeOrder.business_mode == business_mode)
    if q.strip():
        needle = f"%{q.strip()}%"
        stmt = stmt.where(or_(
            ForeignTradeOrder.external_order_no.ilike(needle),
            ForeignTradeOrder.customer_name.ilike(needle),
            ForeignTradeOrder.tracking_no.ilike(needle),
        ))

    orders = db.scalars(stmt).all()
    dealer_ids = {row.dealer_id for row in orders if row.dealer_id is not None}
    dealers = {
        row.id: row
        for row in db.scalars(select(ForeignTradeDealer).where(ForeignTradeDealer.id.in_(dealer_ids))).all()
    } if dealer_ids else {}
    return [order_dict(row, dealers.get(row.dealer_id)) for row in orders]



def list_product_platforms(db: Session, brand: str = "ALSVID") -> list[dict]:
    normalized_brand = brand.strip().upper() or "ALSVID"
    rows = db.scalars(
        select(ForeignTradeProductPlatform)
        .where(func.upper(ForeignTradeProductPlatform.brand) == normalized_brand)
        .order_by(ForeignTradeProductPlatform.display_order, ForeignTradeProductPlatform.code)
    ).all()
    return [product_platform_dict(row) for row in rows]


def list_foreign_products(
    db: Session,
    brand: str = "ALSVID",
    platform_code: str = "",
    q: str = "",
) -> list[dict]:
    normalized_brand = brand.strip().upper() or "ALSVID"
    stmt = select(ForeignTradeProduct).where(
        func.upper(ForeignTradeProduct.brand) == normalized_brand
    )
    if platform_code.strip():
        stmt = stmt.where(ForeignTradeProduct.platform_code == platform_code.strip().upper())
    if q.strip():
        needle = f"%{q.strip()}%"
        stmt = stmt.where(or_(
            ForeignTradeProduct.model_code.ilike(needle),
            ForeignTradeProduct.name.ilike(needle),
            ForeignTradeProduct.name_en.ilike(needle),
            ForeignTradeProduct.external_sku.ilike(needle),
        ))
    rows = db.scalars(
        stmt.order_by(ForeignTradeProduct.platform_code, ForeignTradeProduct.model_code)
    ).all()
    platform_codes = {row.platform_code for row in rows}
    platforms = {
        row.code: row
        for row in db.scalars(
            select(ForeignTradeProductPlatform).where(
                func.upper(ForeignTradeProductPlatform.brand) == normalized_brand,
                ForeignTradeProductPlatform.code.in_(platform_codes),
            )
        ).all()
    } if platform_codes else {}
    sku_ids = {row.sku_id for row in rows if row.sku_id is not None}
    skus = {
        row.id: row
        for row in db.scalars(select(ProductSku).where(ProductSku.id.in_(sku_ids))).all()
    } if sku_ids else {}
    return [foreign_product_dict(row, platforms.get(row.platform_code), skus.get(row.sku_id)) for row in rows]


def overview(db: Session) -> dict:
    orders = db.scalars(select(ForeignTradeOrder)).all()
    channels = db.scalar(select(func.count()).select_from(ForeignTradeChannel)) or 0
    mappings = db.scalar(select(func.count()).select_from(ForeignTradeSkuMapping)) or 0
    pending_mapping = db.scalar(
        select(func.count()).select_from(ForeignTradeSkuMapping).where(ForeignTradeSkuMapping.status != "matched")
    ) or 0
    paid = sum((_decimal(row.paid_amount) - _decimal(row.refund_amount) for row in orders), Decimal("0"))
    profit = sum((order_profit(row) * (_decimal(row.exchange_rate_to_cny) or Decimal("1")) for row in orders), Decimal("0"))
    return {
        "orders": len(orders),
        "b2bOrders": sum(1 for row in orders if row.business_mode == "b2b"),
        "b2cOrders": sum(1 for row in orders if row.business_mode != "b2b"),
        "pendingProcurement": sum(1 for row in orders if row.procurement_status != "done" and row.status not in {"cancelled", "refunded"}),
        "pendingFulfillment": sum(1 for row in orders if row.fulfillment_status != "shipped" and row.status not in {"cancelled", "refunded"}),
        "channels": int(channels),
        "skuMappings": int(mappings),
        "pendingSkuMappings": int(pending_mapping),
        "salesAmount": str(paid),
        "profitCny": str(profit),
    }



def dealer_dict(row: ForeignTradeDealer) -> dict:
    return {
        "id": row.id,
        "code": row.code,
        "companyName": row.company_name,
        "country": row.country,
        "region": row.region,
        "contactName": row.contact_name,
        "email": row.email,
        "phone": row.phone,
        "dealerLevel": row.dealer_level,
        "status": row.status,
        "currency": row.currency,
        "shopifyCompanyId": row.shopify_company_id,
        "shopifyCompanyLocationId": row.shopify_company_location_id,
        "address": row.address,
        "note": row.note,
    }


def _reservation_effective_status(row: ForeignTradeInventoryReservation, now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    if row.status != "active":
        return row.status
    if row.expires_at is not None and row.expires_at <= current:
        return "expired"
    return "active"


def reservation_dict(
    row: ForeignTradeInventoryReservation,
    sku: ProductSku | None = None,
) -> dict:
    effective = _reservation_effective_status(row)
    return {
        "id": row.id,
        "dealerId": row.dealer_id,
        "skuId": row.sku_id,
        "skuCode": sku.sku_code if sku else "",
        "skuName": sku.sku_name if sku else "",
        "quantity": str(row.quantity),
        "reservationKind": row.reservation_kind,
        "referenceNo": row.reference_no,
        "source": row.source,
        "shopifyDraftOrderId": row.shopify_draft_order_id,
        "startsAt": row.starts_at.isoformat() if row.starts_at else None,
        "expiresAt": row.expires_at.isoformat() if row.expires_at else None,
        "status": row.status,
        "effectiveStatus": effective,
        "note": row.note,
    }


def list_dealers(db: Session, q: str = "") -> list[dict]:
    stmt = select(ForeignTradeDealer).order_by(ForeignTradeDealer.id.desc())
    if q.strip():
        needle = f"%{q.strip()}%"
        stmt = stmt.where(or_(
            ForeignTradeDealer.code.ilike(needle),
            ForeignTradeDealer.company_name.ilike(needle),
            ForeignTradeDealer.contact_name.ilike(needle),
            ForeignTradeDealer.email.ilike(needle),
        ))
    return [dealer_dict(row) for row in db.scalars(stmt).all()]


def list_reservations(db: Session, dealer_id: int | None = None) -> list[dict]:
    stmt = select(ForeignTradeInventoryReservation).order_by(ForeignTradeInventoryReservation.id.desc())
    if dealer_id is not None:
        stmt = stmt.where(ForeignTradeInventoryReservation.dealer_id == dealer_id)
    rows = db.scalars(stmt).all()
    sku_ids = {row.sku_id for row in rows}
    skus = {
        row.id: row
        for row in db.scalars(select(ProductSku).where(ProductSku.id.in_(sku_ids))).all()
    } if sku_ids else {}
    return [reservation_dict(row, skus.get(row.sku_id)) for row in rows]


def _active_reservations(db: Session) -> list[ForeignTradeInventoryReservation]:
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(ForeignTradeInventoryReservation).where(
            ForeignTradeInventoryReservation.status == "active"
        )
    ).all()
    return [row for row in rows if row.expires_at is None or row.expires_at > now]


def dealer_inventory(db: Session, dealer_id: int, sku_code: str = "") -> dict:
    dealer = db.get(ForeignTradeDealer, dealer_id)
    if dealer is None:
        raise KeyError("经销商不存在")

    positions = inventory_position_service.current_positions(db)
    actual_by_sku: dict[int, Decimal] = positions["by_sku"]
    reservations = _active_reservations(db)
    total_reserved: dict[int, Decimal] = {}
    dealer_reserved: dict[int, Decimal] = {}
    for row in reservations:
        total_reserved[row.sku_id] = total_reserved.get(row.sku_id, Decimal("0")) + _decimal(row.quantity)
        if row.dealer_id == dealer_id:
            dealer_reserved[row.sku_id] = dealer_reserved.get(row.sku_id, Decimal("0")) + _decimal(row.quantity)

    stmt = select(ProductSku).where(ProductSku.status == "active").order_by(ProductSku.sku_code)
    if sku_code.strip():
        stmt = stmt.where(ProductSku.sku_code == sku_code.strip())
    rows = []
    for sku in db.scalars(stmt).all():
        actual = _decimal(actual_by_sku.get(sku.id, 0))
        reserved_total = total_reserved.get(sku.id, Decimal("0"))
        own_reserved = dealer_reserved.get(sku.id, Decimal("0"))
        public_available = max(actual - reserved_total, Decimal("0"))
        available_to_dealer = public_available + own_reserved
        if not sku_code and actual <= 0 and own_reserved <= 0:
            continue
        rows.append({
            "skuId": sku.id,
            "skuCode": sku.sku_code,
            "skuName": sku.sku_name,
            "actualStock": str(actual),
            "publicAvailable": str(public_available),
            "dealerReserved": str(own_reserved),
            "availableToDealer": str(available_to_dealer),
        })

    return {
        "dealer": dealer_dict(dealer),
        "items": rows,
        "rule": "availableToDealer = publicAvailable + dealerReserved",
    }


def create_reservation(
    db: Session,
    *,
    dealer_id: int,
    sku_code: str,
    quantity: Decimal,
    expires_at: datetime | None,
    reservation_kind: str = "quote",
    reference_no: str = "",
    source: str = "manual",
    shopify_draft_order_id: str = "",
    note: str = "",
) -> ForeignTradeInventoryReservation:
    if quantity <= 0:
        raise ValueError("预留数量必须大于 0")
    dealer = db.get(ForeignTradeDealer, dealer_id)
    if dealer is None:
        raise KeyError("经销商不存在")
    # 同一 SKU 的预留创建必须串行化：否则两个报价并发读取到相同可售量时会一起超额锁库。
    # 锁 ProductSku 主档作为“每 SKU 锁”，真实库存仍来自统一库存总账，不复制库存。
    sku = db.scalar(
        select(ProductSku)
        .where(ProductSku.sku_code == sku_code.strip())
        .with_for_update()
    )
    if sku is None:
        raise KeyError("SKU 不存在")
    if expires_at is not None:
        current = datetime.now(timezone.utc)
        compare_expiry = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=timezone.utc)
        if compare_expiry <= current:
            raise ValueError("库存锁定到期时间必须晚于当前时间")

    inventory = dealer_inventory(db, dealer_id, sku_code=sku.sku_code)
    public_available = _decimal(inventory["items"][0]["publicAvailable"]) if inventory["items"] else Decimal("0")
    if quantity > public_available:
        raise ValueError(f"公共可分配库存不足，当前可新增预留 {public_available}")

    row = ForeignTradeInventoryReservation(
        dealer_id=dealer_id,
        sku_id=sku.id,
        quantity=quantity,
        reservation_kind=reservation_kind,
        reference_no=reference_no,
        source=source,
        shopify_draft_order_id=shopify_draft_order_id,
        starts_at=datetime.now(timezone.utc),
        expires_at=expires_at,
        status="active",
        note=note,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def release_reservation(db: Session, reservation_id: int) -> ForeignTradeInventoryReservation:
    row = db.get(ForeignTradeInventoryReservation, reservation_id)
    if row is None:
        raise KeyError("库存预留不存在")
    if row.status == "active":
        row.status = "released"
        db.commit()
        db.refresh(row)
    return row


def extend_reservation(
    db: Session,
    reservation_id: int,
    expires_at: datetime,
) -> ForeignTradeInventoryReservation:
    row = db.get(ForeignTradeInventoryReservation, reservation_id)
    if row is None:
        raise KeyError("库存预留不存在")
    current = datetime.now(timezone.utc)
    compare_expiry = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=timezone.utc)
    if compare_expiry <= current:
        raise ValueError("新的锁定到期时间必须晚于当前时间")
    if row.status not in {"active", "expired"} and _reservation_effective_status(row) != "expired":
        raise ValueError("只有有效或已到期的预留可以延长")
    row.expires_at = expires_at
    row.status = "active"
    db.commit()
    db.refresh(row)
    return row



def shipment_costs(row: ForeignTradeShipment) -> dict:
    """Calculate estimated import cash requirement and export rebate.

    Duties are calculated from the entered customs/CIF basis and editable rates.
    The actual customs declaration remains authoritative; the system is a planning/reconciliation tool.
    """
    hundred = Decimal("100")
    cif = _decimal(row.declared_value) + _decimal(row.freight_to_eu) + _decimal(row.insurance)
    customs_duty = cif * _decimal(row.customs_rate) / hundred
    anti_dumping_duty = cif * _decimal(row.anti_dumping_rate) / hundred
    countervailing_duty = cif * _decimal(row.countervailing_rate) / hundred
    vat_base = (
        cif
        + customs_duty
        + anti_dumping_duty
        + countervailing_duty
        + _decimal(row.import_vat_additional_base)
    )
    import_vat = vat_base * _decimal(row.import_vat_rate) / hundred
    ancillary = (
        _decimal(row.clearance_fee)
        + _decimal(row.port_fee)
        + _decimal(row.last_mile_fee)
        + _decimal(row.other_import_fee)
    )
    import_tax_total = customs_duty + anti_dumping_duty + countervailing_duty + import_vat
    landed_cash = cif + import_tax_total + ancillary
    landed_cost = landed_cash - (import_vat if row.import_vat_recoverable else Decimal("0"))

    estimated_refund = _decimal(row.export_refund_base_cny) * _decimal(row.export_refund_rate) / hundred
    china_net_estimated = (
        _decimal(row.export_purchase_cost_cny)
        + _decimal(row.domestic_export_cost_cny)
        - estimated_refund
    )
    china_net_actual = (
        _decimal(row.export_purchase_cost_cny)
        + _decimal(row.domestic_export_cost_cny)
        - _decimal(row.actual_export_refund_cny)
    )
    quantity = _decimal(row.quantity)
    per_unit_landed = landed_cost / quantity if quantity > 0 else Decimal("0")

    return {
        "cifValue": str(cif),
        "customsDuty": str(customs_duty),
        "antiDumpingDuty": str(anti_dumping_duty),
        "countervailingDuty": str(countervailing_duty),
        "importVatBase": str(vat_base),
        "importVat": str(import_vat),
        "importTaxTotal": str(import_tax_total),
        "importAncillaryFees": str(ancillary),
        "landedCashRequirement": str(landed_cash),
        "landedCostExRecoverableVat": str(landed_cost),
        "perUnitLandedCostExRecoverableVat": str(per_unit_landed),
        "estimatedExportRefundCny": str(estimated_refund),
        "chinaNetCostEstimatedCny": str(china_net_estimated),
        "chinaNetCostActualCny": str(china_net_actual),
    }


def shipment_dict(row: ForeignTradeShipment) -> dict:
    return {
        "id": row.id,
        "shipmentNo": row.shipment_no,
        "orderNos": row.order_nos or [],
        "brand": row.brand,
        "originCountry": row.origin_country,
        "destinationCountry": row.destination_country,
        "destinationCity": row.destination_city,
        "transportMode": row.transport_mode,
        "incoterm": row.incoterm,
        "status": row.status,
        "exporterLegalEntityId": row.exporter_legal_entity_id,
        "importerKind": row.importer_kind,
        "importerLegalEntityId": row.importer_legal_entity_id,
        "carrier": row.carrier,
        "bookingNo": row.booking_no,
        "billOfLadingNo": row.bill_of_lading_no,
        "containerNo": row.container_no,
        "trackingNo": row.tracking_no,
        "exportCustomsNo": row.export_customs_no,
        "importCustomsNo": row.import_customs_no,
        "commercialInvoiceNo": row.commercial_invoice_no,
        "eoriNo": row.eori_no,
        "hsCode": row.hs_code,
        "cnCode": row.cn_code,
        "manufacturerName": row.manufacturer_name,
        "taricAdditionalCode": row.taric_additional_code,
        "taxRateSource": row.tax_rate_source,
        "taxRateCheckedAt": row.tax_rate_checked_at.isoformat() if row.tax_rate_checked_at else None,
        "currency": row.currency,
        "quantity": str(row.quantity or 0),
        "declaredValue": str(row.declared_value or 0),
        "freightToEu": str(row.freight_to_eu or 0),
        "insurance": str(row.insurance or 0),
        "customsRate": str(row.customs_rate or 0),
        "antiDumpingRate": str(row.anti_dumping_rate or 0),
        "countervailingRate": str(row.countervailing_rate or 0),
        "importVatRate": str(row.import_vat_rate or 0),
        "importVatRecoverable": bool(row.import_vat_recoverable),
        "importVatAdditionalBase": str(row.import_vat_additional_base or 0),
        "clearanceFee": str(row.clearance_fee or 0),
        "portFee": str(row.port_fee or 0),
        "lastMileFee": str(row.last_mile_fee or 0),
        "otherImportFee": str(row.other_import_fee or 0),
        "exportPurchaseCostCny": str(row.export_purchase_cost_cny or 0),
        "domesticExportCostCny": str(row.domestic_export_cost_cny or 0),
        "exportRefundBaseCny": str(row.export_refund_base_cny or 0),
        "exportRefundRate": str(row.export_refund_rate or 0),
        "actualExportRefundCny": str(row.actual_export_refund_cny or 0),
        "exportRefundStatus": row.export_refund_status,
        "exportRefundReceivedAt": row.export_refund_received_at.isoformat() if row.export_refund_received_at else None,
        "eurToCny": str(row.eur_to_cny or 1),
        "etd": row.etd.isoformat() if row.etd else None,
        "eta": row.eta.isoformat() if row.eta else None,
        "departedAt": row.departed_at.isoformat() if row.departed_at else None,
        "arrivedEuAt": row.arrived_eu_at.isoformat() if row.arrived_eu_at else None,
        "customsClearedAt": row.customs_cleared_at.isoformat() if row.customs_cleared_at else None,
        "deliveredAt": row.delivered_at.isoformat() if row.delivered_at else None,
        "milestones": row.milestones or [],
        "documents": row.documents or [],
        "note": row.note,
        "costs": shipment_costs(row),
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_shipments(db: Session, q: str = "", status: str = "") -> list[dict]:
    stmt = select(ForeignTradeShipment).order_by(ForeignTradeShipment.id.desc()).limit(1000)
    if status:
        stmt = stmt.where(ForeignTradeShipment.status == status)
    if q.strip():
        needle = f"%{q.strip()}%"
        stmt = stmt.where(or_(
            ForeignTradeShipment.shipment_no.ilike(needle),
            ForeignTradeShipment.booking_no.ilike(needle),
            ForeignTradeShipment.bill_of_lading_no.ilike(needle),
            ForeignTradeShipment.container_no.ilike(needle),
            ForeignTradeShipment.tracking_no.ilike(needle),
            ForeignTradeShipment.manufacturer_name.ilike(needle),
        ))
    return [shipment_dict(row) for row in db.scalars(stmt).all()]
