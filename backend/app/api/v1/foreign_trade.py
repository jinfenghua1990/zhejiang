"""外贸工作台 API。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.catalog import ProductSku
from app.models.foreign_trade import (
    ForeignTradeChannel,
    ForeignTradeDealer,
    ForeignTradeOrder,
    ForeignTradeProduct,
    ForeignTradeProductPlatform,
    ForeignTradeShipment,
    ForeignTradeSkuMapping,
)
from app.services import finance_center_service
from app.services import finance_projection_service
from app.services import foreign_trade_service as service

router = APIRouter(prefix="/foreign-trade", tags=["外贸工作台"])


def _money(value: str | int | float | Decimal | None) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="金额格式不正确") from exc


@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> dict:
    return service.overview(db)


class ChannelBody(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    channel_type: str = Field(default="manual", max_length=32)
    brand: str = Field(default="", max_length=128)
    currency: str = Field(default="EUR", max_length=8)
    countries: list[str] = []
    enabled: bool = True
    connected: bool = False
    note: str = ""


@router.get("/channels")
def channels(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(ForeignTradeChannel).order_by(ForeignTradeChannel.id.desc())).all()
    return {"items": [service.channel_dict(row) for row in rows]}


@router.post("/channels", status_code=201)
def create_channel(body: ChannelBody, db: Session = Depends(get_db)) -> dict:
    row = ForeignTradeChannel(**body.model_dump())
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="渠道编码已存在") from exc
    db.refresh(row)
    return service.channel_dict(row)


@router.put("/channels/{channel_id}")
def update_channel(channel_id: int, body: ChannelBody, db: Session = Depends(get_db)) -> dict:
    row = db.get(ForeignTradeChannel, channel_id)
    if not row:
        raise HTTPException(status_code=404, detail="渠道不存在")
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="渠道编码已存在") from exc
    db.refresh(row)
    return service.channel_dict(row)


@router.delete("/channels/{channel_id}")
def delete_channel(channel_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(ForeignTradeChannel, channel_id)
    if not row:
        raise HTTPException(status_code=404, detail="渠道不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


class SkuBody(BaseModel):
    channel_code: str = Field(min_length=1, max_length=64)
    external_sku: str = Field(min_length=1, max_length=128)
    internal_sku: str = Field(default="", max_length=128)
    product_name: str = Field(default="", max_length=512)
    status: str = Field(default="pending", max_length=16)
    note: str = ""


@router.get("/sku-mappings")
def sku_mappings(q: str = Query("", max_length=200), db: Session = Depends(get_db)) -> dict:
    stmt = select(ForeignTradeSkuMapping).order_by(ForeignTradeSkuMapping.id.desc())
    if q.strip():
        needle = f"%{q.strip()}%"
        stmt = stmt.where(
            (ForeignTradeSkuMapping.external_sku.ilike(needle))
            | (ForeignTradeSkuMapping.internal_sku.ilike(needle))
            | (ForeignTradeSkuMapping.product_name.ilike(needle))
        )
    return {"items": [service.sku_dict(row) for row in db.scalars(stmt).all()]}


@router.post("/sku-mappings", status_code=201)
def create_sku_mapping(body: SkuBody, db: Session = Depends(get_db)) -> dict:
    row = ForeignTradeSkuMapping(**body.model_dump())
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="该渠道的海外 SKU 已存在") from exc
    db.refresh(row)
    return service.sku_dict(row)


@router.put("/sku-mappings/{mapping_id}")
def update_sku_mapping(mapping_id: int, body: SkuBody, db: Session = Depends(get_db)) -> dict:
    row = db.get(ForeignTradeSkuMapping, mapping_id)
    if not row:
        raise HTTPException(status_code=404, detail="SKU 映射不存在")
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return service.sku_dict(row)


@router.delete("/sku-mappings/{mapping_id}")
def delete_sku_mapping(mapping_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(ForeignTradeSkuMapping, mapping_id)
    if not row:
        raise HTTPException(status_code=404, detail="SKU 映射不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/products/platforms")
def product_platforms(
    brand: str = Query("ALSVID", max_length=128),
    db: Session = Depends(get_db),
) -> dict:
    return {"items": service.list_product_platforms(db, brand=brand)}


class ProductBody(BaseModel):
    brand: str = Field(default="ALSVID", min_length=1, max_length=128)
    platform_code: str = Field(min_length=1, max_length=32)
    model_code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=256)
    name_en: str = Field(default="", max_length=256)
    sku_id: int | None = None
    external_sku: str = Field(default="", max_length=128)
    status: Literal["draft", "planned", "active", "archived"] = "draft"
    countries: list[str] = Field(default_factory=list)
    currency: str = Field(default="EUR", max_length=8)
    note: str = ""


def _product_values(db: Session, body: ProductBody) -> dict:
    brand = body.brand.strip().upper() or "ALSVID"
    platform_code = body.platform_code.strip().upper()
    model_code = body.model_code.strip().upper()
    platform = db.scalar(select(ForeignTradeProductPlatform).where(
        func.upper(ForeignTradeProductPlatform.brand) == brand,
        ForeignTradeProductPlatform.code == platform_code,
    ))
    if platform is None:
        raise HTTPException(status_code=404, detail="技术平台不存在，请先维护平台主档")
    if body.sku_id is not None and db.get(ProductSku, body.sku_id) is None:
        raise HTTPException(status_code=404, detail="绑定的中台 SKU 不存在")
    countries = list(dict.fromkeys(
        value.strip().upper() for value in body.countries if value.strip()
    ))
    return {
        "brand": brand,
        "platform_code": platform_code,
        "model_code": model_code,
        "name": body.name.strip(),
        "name_en": body.name_en.strip(),
        "sku_id": body.sku_id,
        "external_sku": body.external_sku.strip(),
        "status": body.status,
        "countries": countries,
        "currency": body.currency.strip().upper() or "EUR",
        "note": body.note.strip(),
    }


@router.get("/products")
def products(
    brand: str = Query("ALSVID", max_length=128),
    platform_code: str = Query("", max_length=32),
    q: str = Query("", max_length=200),
    db: Session = Depends(get_db),
) -> dict:
    return {
        "items": service.list_foreign_products(
            db, brand=brand, platform_code=platform_code, q=q
        )
    }


@router.post("/products", status_code=201)
def create_product(body: ProductBody, db: Session = Depends(get_db)) -> dict:
    values = _product_values(db, body)
    existing = db.scalar(select(ForeignTradeProduct).where(
        ForeignTradeProduct.brand == values["brand"],
        ForeignTradeProduct.model_code == values["model_code"],
    ))
    if existing is not None:
        raise HTTPException(status_code=409, detail="该品牌的产品型号已存在")
    row = ForeignTradeProduct(**values)
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="该品牌的产品型号已存在") from exc
    db.refresh(row)
    return service.list_foreign_products(db, brand=row.brand, q=row.model_code)[0]


@router.put("/products/{product_id}")
def update_product(product_id: int, body: ProductBody, db: Session = Depends(get_db)) -> dict:
    row = db.get(ForeignTradeProduct, product_id)
    if row is None:
        raise HTTPException(status_code=404, detail="外贸产品不存在")
    values = _product_values(db, body)
    existing = db.scalar(select(ForeignTradeProduct).where(
        ForeignTradeProduct.brand == values["brand"],
        ForeignTradeProduct.model_code == values["model_code"],
        ForeignTradeProduct.id != row.id,
    ))
    if existing is not None:
        raise HTTPException(status_code=409, detail="该品牌的产品型号已存在")
    for key, value in values.items():
        setattr(row, key, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="该品牌的产品型号已存在") from exc
    db.refresh(row)
    return service.list_foreign_products(db, brand=row.brand, q=row.model_code)[0]


@router.delete("/products/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(ForeignTradeProduct, product_id)
    if row is None:
        raise HTTPException(status_code=404, detail="外贸产品不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


class OrderBody(BaseModel):
    channel_code: str = Field(min_length=1, max_length=64)
    external_order_no: str = Field(min_length=1, max_length=128)
    business_mode: Literal["b2c", "b2b"] = "b2c"
    dealer_id: int | None = None
    brand: str = Field(default="", max_length=128)
    country: str = Field(default="", max_length=64)
    currency: str = Field(default="EUR", max_length=8)
    gross_amount: str = "0"
    discount_amount: str = "0"
    shipping_income: str = "0"
    paid_amount: str = "0"
    refund_amount: str = "0"
    payment_fee: str = "0"
    purchase_cost: str = "0"
    logistics_cost: str = "0"
    exchange_rate_to_cny: str = "1"
    status: str = Field(default="pending", max_length=24)
    procurement_status: str = Field(default="pending", max_length=24)
    fulfillment_status: str = Field(default="pending", max_length=24)
    payment_status: str = Field(default="unpaid", max_length=24)
    seller_legal_entity_id: int | None = None
    customer_name: str = Field(default="", max_length=128)
    customer_email: str = Field(default="", max_length=256)
    ship_to: str = ""
    carrier: str = Field(default="", max_length=128)
    tracking_no: str = Field(default="", max_length=128)
    ordered_at: datetime | None = None
    paid_at: datetime | None = None
    shipped_at: datetime | None = None
    items: list[dict] = []
    note: str = ""


def _apply_order(db: Session, row: ForeignTradeOrder, body: OrderBody) -> ForeignTradeDealer | None:
    values = body.model_dump()
    dealer: ForeignTradeDealer | None = None
    if body.business_mode == "b2b":
        if body.dealer_id is None:
            raise HTTPException(status_code=400, detail="B2B 订单必须绑定经销商")
        dealer = db.get(ForeignTradeDealer, body.dealer_id)
        if dealer is None:
            raise HTTPException(status_code=404, detail="经销商不存在")
        if dealer.status != "active":
            raise HTTPException(status_code=400, detail="B2B 订单只能绑定启用中的经销商")
    else:
        values["dealer_id"] = None

    if body.seller_legal_entity_id is None:
        values["seller_legal_entity_id"] = finance_center_service.resolve_entity(db).id
    else:
        finance_center_service.resolve_entity(db, body.seller_legal_entity_id)

    for key in (
        "gross_amount", "discount_amount", "shipping_income", "paid_amount", "refund_amount",
        "payment_fee", "purchase_cost", "logistics_cost", "exchange_rate_to_cny",
    ):
        values[key] = _money(values[key])
    for key, value in values.items():
        setattr(row, key, value)
    return dealer


@router.get("/orders")
def orders(
    q: str = Query("", max_length=200),
    status: str = Query("", max_length=24),
    channel_code: str = Query("", max_length=64),
    brand: str = Query("", max_length=128),
    business_mode: Literal["", "b2c", "b2b"] = Query(""),
    db: Session = Depends(get_db),
) -> dict:
    return {
        "items": service.list_orders(
            db,
            q=q,
            status=status,
            channel_code=channel_code,
            brand=brand,
            business_mode=business_mode,
        )
    }


@router.post("/orders", status_code=201)
def create_order(body: OrderBody, db: Session = Depends(get_db)) -> dict:
    row = ForeignTradeOrder(channel_code=body.channel_code, external_order_no=body.external_order_no)
    dealer = _apply_order(db, row, body)
    db.add(row)
    try:
        db.flush()
        finance_projection_service.project_foreign_order(db, row)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="该渠道订单号已存在") from exc
    db.refresh(row)
    return service.order_dict(row, dealer)


@router.put("/orders/{order_id}")
def update_order(order_id: int, body: OrderBody, db: Session = Depends(get_db)) -> dict:
    row = db.get(ForeignTradeOrder, order_id)
    if not row:
        raise HTTPException(status_code=404, detail="外贸订单不存在")
    dealer = _apply_order(db, row, body)
    try:
        finance_projection_service.project_foreign_order(db, row)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="该渠道订单号已存在") from exc
    db.refresh(row)
    return service.order_dict(row, dealer)


@router.delete("/orders/{order_id}")
def delete_order(order_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(ForeignTradeOrder, order_id)
    if not row:
        raise HTTPException(status_code=404, detail="外贸订单不存在")
    finance_projection_service.delete_projected_source(db, "foreign_order", str(row.id))
    db.delete(row)
    db.commit()
    return {"ok": True}



class DealerBody(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    company_name: str = Field(min_length=1, max_length=256)
    country: str = Field(default="", max_length=64)
    region: str = Field(default="", max_length=128)
    contact_name: str = Field(default="", max_length=128)
    email: str = Field(default="", max_length=256)
    phone: str = Field(default="", max_length=64)
    dealer_level: str = Field(default="standard", max_length=32)
    status: str = Field(default="active", max_length=24)
    currency: str = Field(default="EUR", max_length=8)
    shopify_company_id: str = Field(default="", max_length=128)
    shopify_company_location_id: str = Field(default="", max_length=128)
    address: str = ""
    note: str = ""


@router.get("/b2b/dealers")
def dealers(q: str = Query("", max_length=200), db: Session = Depends(get_db)) -> dict:
    return {"items": service.list_dealers(db, q=q)}


@router.post("/b2b/dealers", status_code=201)
def create_dealer(body: DealerBody, db: Session = Depends(get_db)) -> dict:
    row = ForeignTradeDealer(**body.model_dump())
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="经销商编码已存在") from exc
    db.refresh(row)
    return service.dealer_dict(row)


@router.put("/b2b/dealers/{dealer_id}")
def update_dealer(dealer_id: int, body: DealerBody, db: Session = Depends(get_db)) -> dict:
    row = db.get(ForeignTradeDealer, dealer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="经销商不存在")
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="经销商编码已存在") from exc
    db.refresh(row)
    return service.dealer_dict(row)


@router.get("/b2b/dealers/{dealer_id}/inventory")
def dealer_inventory(
    dealer_id: int,
    sku_code: str = Query("", max_length=128),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return service.dealer_inventory(db, dealer_id, sku_code=sku_code)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc


@router.get("/b2b/dealers/{dealer_id}/reservations")
def dealer_reservations(dealer_id: int, db: Session = Depends(get_db)) -> dict:
    if db.get(ForeignTradeDealer, dealer_id) is None:
        raise HTTPException(status_code=404, detail="经销商不存在")
    return {"items": service.list_reservations(db, dealer_id=dealer_id)}


class ReservationBody(BaseModel):
    dealer_id: int
    sku_code: str = Field(min_length=1, max_length=128)
    quantity: str
    expires_at: datetime | None = None
    reservation_kind: str = Field(default="quote", max_length=24)
    reference_no: str = Field(default="", max_length=128)
    source: str = Field(default="manual", max_length=32)
    shopify_draft_order_id: str = Field(default="", max_length=128)
    note: str = ""


@router.post("/b2b/reservations", status_code=201)
def create_reservation(body: ReservationBody, db: Session = Depends(get_db)) -> dict:
    try:
        quantity = Decimal(body.quantity)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="预留数量格式不正确") from exc
    try:
        row = service.create_reservation(
            db,
            dealer_id=body.dealer_id,
            sku_code=body.sku_code,
            quantity=quantity,
            expires_at=body.expires_at,
            reservation_kind=body.reservation_kind,
            reference_no=body.reference_no,
            source=body.source,
            shopify_draft_order_id=body.shopify_draft_order_id,
            note=body.note,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return service.list_reservations(db, dealer_id=row.dealer_id)[0]


@router.post("/b2b/reservations/{reservation_id}/release")
def release_reservation(reservation_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        row = service.release_reservation(db, reservation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    return {"ok": True, "id": row.id, "status": row.status}


class ExtendReservationBody(BaseModel):
    expires_at: datetime


@router.post("/b2b/reservations/{reservation_id}/extend")
def extend_reservation(
    reservation_id: int,
    body: ExtendReservationBody,
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = service.extend_reservation(db, reservation_id, body.expires_at)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "id": row.id, "expiresAt": row.expires_at.isoformat() if row.expires_at else None}



class ShipmentBody(BaseModel):
    shipment_no: str = Field(min_length=1, max_length=64)
    order_nos: list[str] = []
    brand: str = Field(default="", max_length=128)
    origin_country: str = Field(default="CN", max_length=64)
    destination_country: str = Field(default="AT", max_length=64)
    destination_city: str = Field(default="", max_length=128)
    transport_mode: str = Field(default="sea", max_length=24)
    incoterm: str = Field(default="FOB", max_length=16)
    status: str = Field(default="preparing", max_length=32)
    exporter_legal_entity_id: int | None = None
    importer_kind: Literal["external_customer", "dealer", "own_entity", "agent"] = "external_customer"
    importer_legal_entity_id: int | None = None

    carrier: str = Field(default="", max_length=128)
    booking_no: str = Field(default="", max_length=128)
    bill_of_lading_no: str = Field(default="", max_length=128)
    container_no: str = Field(default="", max_length=128)
    tracking_no: str = Field(default="", max_length=128)

    export_customs_no: str = Field(default="", max_length=128)
    import_customs_no: str = Field(default="", max_length=128)
    commercial_invoice_no: str = Field(default="", max_length=128)
    eori_no: str = Field(default="", max_length=128)

    hs_code: str = Field(default="", max_length=32)
    cn_code: str = Field(default="", max_length=32)
    manufacturer_name: str = Field(default="", max_length=256)
    taric_additional_code: str = Field(default="", max_length=32)
    tax_rate_source: str = ""
    tax_rate_checked_at: datetime | None = None

    currency: str = Field(default="EUR", max_length=8)
    quantity: str = "0"
    declared_value: str = "0"
    freight_to_eu: str = "0"
    insurance: str = "0"

    customs_rate: str = "0"
    anti_dumping_rate: str = "0"
    countervailing_rate: str = "0"
    import_vat_rate: str = "20"
    import_vat_recoverable: bool = True
    import_vat_additional_base: str = "0"

    clearance_fee: str = "0"
    port_fee: str = "0"
    last_mile_fee: str = "0"
    other_import_fee: str = "0"

    export_purchase_cost_cny: str = "0"
    domestic_export_cost_cny: str = "0"
    export_refund_base_cny: str = "0"
    export_refund_rate: str = "0"
    actual_export_refund_cny: str = "0"
    export_refund_status: str = Field(default="pending", max_length=24)
    export_refund_received_at: datetime | None = None
    eur_to_cny: str = "1"

    etd: datetime | None = None
    eta: datetime | None = None
    departed_at: datetime | None = None
    arrived_eu_at: datetime | None = None
    customs_cleared_at: datetime | None = None
    delivered_at: datetime | None = None

    milestones: list[dict] = []
    documents: list[dict] = []
    note: str = ""


_SHIPMENT_DECIMAL_FIELDS = {
    "quantity",
    "declared_value",
    "freight_to_eu",
    "insurance",
    "customs_rate",
    "anti_dumping_rate",
    "countervailing_rate",
    "import_vat_rate",
    "import_vat_additional_base",
    "clearance_fee",
    "port_fee",
    "last_mile_fee",
    "other_import_fee",
    "export_purchase_cost_cny",
    "domestic_export_cost_cny",
    "export_refund_base_cny",
    "export_refund_rate",
    "actual_export_refund_cny",
    "eur_to_cny",
}


def _apply_shipment(db: Session, row: ForeignTradeShipment, body: ShipmentBody) -> None:
    values = body.model_dump()
    if body.exporter_legal_entity_id is None:
        values["exporter_legal_entity_id"] = finance_center_service.resolve_entity(db).id
    else:
        finance_center_service.resolve_entity(db, body.exporter_legal_entity_id)

    if body.importer_kind == "own_entity":
        if body.importer_legal_entity_id is None:
            raise HTTPException(status_code=400, detail="进口责任方为我方主体时，必须选择进口公司主体")
        finance_center_service.resolve_entity(db, body.importer_legal_entity_id)
    else:
        values["importer_legal_entity_id"] = None
    for key in _SHIPMENT_DECIMAL_FIELDS:
        values[key] = _money(values[key])
    if values["quantity"] < 0:
        raise HTTPException(status_code=400, detail="出运数量不能为负数")
    for rate_key in ("customs_rate", "anti_dumping_rate", "countervailing_rate", "import_vat_rate", "export_refund_rate"):
        if values[rate_key] < 0:
            raise HTTPException(status_code=400, detail="税率不能为负数")
    values["order_nos"] = [str(value).strip() for value in values["order_nos"] if str(value).strip()]
    for key, value in values.items():
        setattr(row, key, value)


@router.get("/shipments")
def shipments(
    q: str = Query("", max_length=200),
    status: str = Query("", max_length=32),
    db: Session = Depends(get_db),
) -> dict:
    return {"items": service.list_shipments(db, q=q, status=status)}


@router.post("/shipments", status_code=201)
def create_shipment(body: ShipmentBody, db: Session = Depends(get_db)) -> dict:
    row = ForeignTradeShipment(shipment_no=body.shipment_no)
    _apply_shipment(db, row, body)
    db.add(row)
    try:
        db.flush()
        finance_projection_service.project_foreign_shipment(db, row)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="出运单号已存在") from exc
    db.refresh(row)
    return service.shipment_dict(row)


@router.put("/shipments/{shipment_id}")
def update_shipment(shipment_id: int, body: ShipmentBody, db: Session = Depends(get_db)) -> dict:
    row = db.get(ForeignTradeShipment, shipment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="出运单不存在")
    _apply_shipment(db, row, body)
    try:
        finance_projection_service.project_foreign_shipment(db, row)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="出运单号已存在") from exc
    db.refresh(row)
    return service.shipment_dict(row)


@router.delete("/shipments/{shipment_id}")
def delete_shipment(shipment_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(ForeignTradeShipment, shipment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="出运单不存在")
    finance_projection_service.delete_projected_source(db, "foreign_shipment", str(row.id))
    db.delete(row)
    db.commit()
    return {"ok": True}


class ShipmentMilestoneBody(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    label: str = Field(default="", max_length=128)
    location: str = Field(default="", max_length=256)
    occurred_at: datetime | None = None
    note: str = ""


_MILESTONE_STATUS = {
    "prepared": "preparing",
    "picked_up": "picked_up",
    "export_declared": "export_customs",
    "export_released": "export_released",
    "departed_china": "departed",
    "in_transit": "in_transit",
    "arrived_eu": "arrived_eu",
    "import_declared": "import_customs",
    "customs_cleared": "customs_cleared",
    "last_mile": "last_mile",
    "delivered": "delivered",
}


@router.post("/shipments/{shipment_id}/milestones")
def add_shipment_milestone(
    shipment_id: int,
    body: ShipmentMilestoneBody,
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(ForeignTradeShipment, shipment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="出运单不存在")
    occurred = body.occurred_at or datetime.now().astimezone()
    milestones = list(row.milestones or [])
    milestones.append({
        "code": body.code,
        "label": body.label,
        "location": body.location,
        "occurredAt": occurred.isoformat(),
        "note": body.note,
    })
    row.milestones = milestones
    if body.code in _MILESTONE_STATUS:
        row.status = _MILESTONE_STATUS[body.code]
    if body.code == "departed_china":
        row.departed_at = occurred
    elif body.code == "arrived_eu":
        row.arrived_eu_at = occurred
    elif body.code == "customs_cleared":
        row.customs_cleared_at = occurred
    elif body.code == "delivered":
        row.delivered_at = occurred
    finance_projection_service.project_foreign_shipment(db, row)
    db.commit()
    db.refresh(row)
    return service.shipment_dict(row)
