"""外贸工作台核心模型：渠道、海外 SKU 映射、外贸订单。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin

MONEY = Numeric(18, 4)
RATE = Numeric(18, 6)


class ForeignTradeChannel(Base, PkMixin, TimestampMixin):
    __tablename__ = "foreign_trade_channels"
    __table_args__ = (UniqueConstraint("code", name="uq_foreign_trade_channels_code"),)

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(32), default="manual")
    brand: Mapped[str] = mapped_column(String(128), default="")
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    countries: Mapped[list] = mapped_column(JSONB, default=list)
    enabled: Mapped[bool] = mapped_column(default=True)
    connected: Mapped[bool] = mapped_column(default=False)
    note: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class ForeignTradeSkuMapping(Base, PkMixin, TimestampMixin):
    __tablename__ = "foreign_trade_sku_mappings"
    __table_args__ = (
        UniqueConstraint("channel_code", "external_sku", name="uq_foreign_trade_sku_channel_external"),
        Index("ix_foreign_trade_sku_channel", "channel_code"),
        Index("ix_foreign_trade_sku_internal", "internal_sku"),
    )

    channel_code: Mapped[str] = mapped_column(String(64), nullable=False)
    external_sku: Mapped[str] = mapped_column(String(128), nullable=False)
    internal_sku: Mapped[str] = mapped_column(String(128), default="")
    product_name: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    note: Mapped[str] = mapped_column(Text, default="")


class ForeignTradeProductPlatform(Base, PkMixin, TimestampMixin):
    """外贸品牌技术平台主档，例如 ALSVID 的 FC1/FT1/CT1/GT1。"""

    __tablename__ = "foreign_trade_product_platforms"
    __table_args__ = (
        UniqueConstraint("brand", "code", name="uq_foreign_trade_product_platform_brand_code"),
        Index("ix_foreign_trade_product_platform_brand", "brand"),
        Index("ix_foreign_trade_product_platform_order", "display_order"),
    )

    brand: Mapped[str] = mapped_column(String(128), nullable=False, default="ALSVID")
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    name_en: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class ForeignTradeProduct(Base, PkMixin, TimestampMixin):
    """外贸产品主档；可先建产品，后续再绑定共用的中台 ProductSku。"""

    __tablename__ = "foreign_trade_products"
    __table_args__ = (
        UniqueConstraint("brand", "model_code", name="uq_foreign_trade_products_brand_model"),
        Index("ix_foreign_trade_products_brand_platform", "brand", "platform_code"),
        Index("ix_foreign_trade_products_status", "status"),
        Index("ix_foreign_trade_products_sku_id", "sku_id"),
    )

    brand: Mapped[str] = mapped_column(String(128), nullable=False, default="ALSVID")
    platform_code: Mapped[str] = mapped_column(String(32), nullable=False)
    model_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    name_en: Mapped[str] = mapped_column(String(256), default="")
    sku_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("product_skus.id", ondelete="SET NULL"), nullable=True
    )
    external_sku: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(24), default="draft")
    countries: Mapped[list] = mapped_column(JSONB, default=list)
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    note: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class ForeignTradeOrder(Base, PkMixin, TimestampMixin):
    __tablename__ = "foreign_trade_orders"
    __table_args__ = (
        UniqueConstraint("channel_code", "external_order_no", name="uq_foreign_trade_order_channel_external"),
        Index("ix_foreign_trade_orders_status", "status"),
        Index("ix_foreign_trade_orders_business_mode", "business_mode"),
        Index("ix_foreign_trade_orders_channel", "channel_code"),
        Index("ix_foreign_trade_orders_fulfillment", "fulfillment_status"),
        Index("ix_foreign_trade_orders_ordered_at", "ordered_at"),
        Index("ix_foreign_trade_orders_dealer_id", "dealer_id"),
        Index("ix_foreign_trade_orders_seller_legal_entity_id", "seller_legal_entity_id"),
    )

    channel_code: Mapped[str] = mapped_column(String(64), nullable=False)
    external_order_no: Mapped[str] = mapped_column(String(128), nullable=False)
    business_mode: Mapped[str] = mapped_column(String(16), default="b2c")
    dealer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("foreign_trade_dealers.id", ondelete="SET NULL"), nullable=True
    )
    brand: Mapped[str] = mapped_column(String(128), default="")
    country: Mapped[str] = mapped_column(String(64), default="")
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    gross_amount: Mapped[Decimal] = mapped_column(MONEY, default=0)
    discount_amount: Mapped[Decimal] = mapped_column(MONEY, default=0)
    shipping_income: Mapped[Decimal] = mapped_column(MONEY, default=0)
    paid_amount: Mapped[Decimal] = mapped_column(MONEY, default=0)
    refund_amount: Mapped[Decimal] = mapped_column(MONEY, default=0)
    payment_fee: Mapped[Decimal] = mapped_column(MONEY, default=0)
    purchase_cost: Mapped[Decimal] = mapped_column(MONEY, default=0)
    logistics_cost: Mapped[Decimal] = mapped_column(MONEY, default=0)
    exchange_rate_to_cny: Mapped[Decimal] = mapped_column(RATE, default=1)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    procurement_status: Mapped[str] = mapped_column(String(24), default="pending")
    fulfillment_status: Mapped[str] = mapped_column(String(24), default="pending")
    payment_status: Mapped[str] = mapped_column(String(24), default="unpaid")
    seller_legal_entity_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("finance_legal_entities.id", ondelete="RESTRICT"), nullable=True
    )
    customer_name: Mapped[str] = mapped_column(String(128), default="")
    customer_email: Mapped[str] = mapped_column(String(256), default="")
    ship_to: Mapped[str] = mapped_column(Text, default="")
    carrier: Mapped[str] = mapped_column(String(128), default="")
    tracking_no: Mapped[str] = mapped_column(String(128), default="")
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    items: Mapped[list] = mapped_column(JSONB, default=list)
    note: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class ForeignTradeDealer(Base, PkMixin, TimestampMixin):
    """B2B 经销商客户主档。Shopify Company / Location 只是外部映射，不是主数据。"""

    __tablename__ = "foreign_trade_dealers"
    __table_args__ = (
        UniqueConstraint("code", name="uq_foreign_trade_dealers_code"),
        Index("ix_foreign_trade_dealers_company", "company_name"),
        Index("ix_foreign_trade_dealers_shopify_location", "shopify_company_location_id"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    company_name: Mapped[str] = mapped_column(String(256), nullable=False)
    country: Mapped[str] = mapped_column(String(64), default="")
    region: Mapped[str] = mapped_column(String(128), default="")
    contact_name: Mapped[str] = mapped_column(String(128), default="")
    email: Mapped[str] = mapped_column(String(256), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    dealer_level: Mapped[str] = mapped_column(String(32), default="standard")
    status: Mapped[str] = mapped_column(String(24), default="active")
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    shopify_company_id: Mapped[str] = mapped_column(String(128), default="")
    shopify_company_location_id: Mapped[str] = mapped_column(String(128), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class ForeignTradeInventoryReservation(Base, PkMixin, TimestampMixin):
    """B2B 专属库存预留。只影响可售量，不修改真实库存总账。"""

    __tablename__ = "foreign_trade_inventory_reservations"
    __table_args__ = (
        Index("ix_ft_inventory_reservation_dealer", "dealer_id"),
        Index("ix_ft_inventory_reservation_sku", "sku_id"),
        Index("ix_ft_inventory_reservation_expires", "expires_at"),
        Index("ix_ft_inventory_reservation_status", "status"),
        Index("ix_ft_inventory_reservation_reference", "reference_no"),
    )

    dealer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("foreign_trade_dealers.id", ondelete="CASCADE"), nullable=False
    )
    sku_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("product_skus.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    reservation_kind: Mapped[str] = mapped_column(String(24), default="quote")
    reference_no: Mapped[str] = mapped_column(String(128), default="")
    source: Mapped[str] = mapped_column(String(32), default="manual")
    shopify_draft_order_id: Mapped[str] = mapped_column(String(128), default="")
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="active")
    note: Mapped[str] = mapped_column(Text, default="")



class ForeignTradeShipment(Base, PkMixin, TimestampMixin):
    """外贸出运单：跟踪中国出口到海外签收的物流、清关、税费和退税。"""

    __tablename__ = "foreign_trade_shipments"
    __table_args__ = (
        UniqueConstraint("shipment_no", name="uq_foreign_trade_shipments_no"),
        Index("ix_foreign_trade_shipments_status", "status"),
        Index("ix_foreign_trade_shipments_tracking_no", "tracking_no"),
        Index("ix_foreign_trade_shipments_bl", "bill_of_lading_no"),
        Index("ix_foreign_trade_shipments_container", "container_no"),
        Index("ix_foreign_trade_shipments_eta", "eta"),
        Index("ix_foreign_trade_shipments_exporter_legal_entity_id", "exporter_legal_entity_id"),
        Index("ix_foreign_trade_shipments_importer_kind", "importer_kind"),
        Index("ix_foreign_trade_shipments_importer_legal_entity_id", "importer_legal_entity_id"),
    )

    shipment_no: Mapped[str] = mapped_column(String(64), nullable=False)
    order_nos: Mapped[list] = mapped_column(JSONB, default=list)

    brand: Mapped[str] = mapped_column(String(128), default="")
    origin_country: Mapped[str] = mapped_column(String(64), default="CN")
    destination_country: Mapped[str] = mapped_column(String(64), default="AT")
    destination_city: Mapped[str] = mapped_column(String(128), default="")
    transport_mode: Mapped[str] = mapped_column(String(24), default="sea")
    incoterm: Mapped[str] = mapped_column(String(16), default="FOB")
    status: Mapped[str] = mapped_column(String(32), default="preparing")
    exporter_legal_entity_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("finance_legal_entities.id", ondelete="RESTRICT"), nullable=True
    )
    importer_kind: Mapped[str] = mapped_column(String(24), default="external_customer")
    importer_legal_entity_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("finance_legal_entities.id", ondelete="RESTRICT"), nullable=True
    )

    carrier: Mapped[str] = mapped_column(String(128), default="")
    booking_no: Mapped[str] = mapped_column(String(128), default="")
    bill_of_lading_no: Mapped[str] = mapped_column(String(128), default="")
    container_no: Mapped[str] = mapped_column(String(128), default="")
    tracking_no: Mapped[str] = mapped_column(String(128), default="")

    export_customs_no: Mapped[str] = mapped_column(String(128), default="")
    import_customs_no: Mapped[str] = mapped_column(String(128), default="")
    commercial_invoice_no: Mapped[str] = mapped_column(String(128), default="")
    eori_no: Mapped[str] = mapped_column(String(128), default="")

    hs_code: Mapped[str] = mapped_column(String(32), default="")
    cn_code: Mapped[str] = mapped_column(String(32), default="")
    manufacturer_name: Mapped[str] = mapped_column(String(256), default="")
    taric_additional_code: Mapped[str] = mapped_column(String(32), default="")
    tax_rate_source: Mapped[str] = mapped_column(Text, default="")
    tax_rate_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    declared_value: Mapped[Decimal] = mapped_column(MONEY, default=0)
    freight_to_eu: Mapped[Decimal] = mapped_column(MONEY, default=0)
    insurance: Mapped[Decimal] = mapped_column(MONEY, default=0)

    customs_rate: Mapped[Decimal] = mapped_column(RATE, default=0)
    anti_dumping_rate: Mapped[Decimal] = mapped_column(RATE, default=0)
    countervailing_rate: Mapped[Decimal] = mapped_column(RATE, default=0)
    import_vat_rate: Mapped[Decimal] = mapped_column(RATE, default=20)
    import_vat_recoverable: Mapped[bool] = mapped_column(Boolean, default=True)
    import_vat_additional_base: Mapped[Decimal] = mapped_column(MONEY, default=0)

    clearance_fee: Mapped[Decimal] = mapped_column(MONEY, default=0)
    port_fee: Mapped[Decimal] = mapped_column(MONEY, default=0)
    last_mile_fee: Mapped[Decimal] = mapped_column(MONEY, default=0)
    other_import_fee: Mapped[Decimal] = mapped_column(MONEY, default=0)

    export_purchase_cost_cny: Mapped[Decimal] = mapped_column(MONEY, default=0)
    domestic_export_cost_cny: Mapped[Decimal] = mapped_column(MONEY, default=0)
    export_refund_base_cny: Mapped[Decimal] = mapped_column(MONEY, default=0)
    export_refund_rate: Mapped[Decimal] = mapped_column(RATE, default=0)
    actual_export_refund_cny: Mapped[Decimal] = mapped_column(MONEY, default=0)
    export_refund_status: Mapped[str] = mapped_column(String(24), default="pending")
    export_refund_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    eur_to_cny: Mapped[Decimal] = mapped_column(RATE, default=1)

    etd: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    eta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    departed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    arrived_eu_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    customs_cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    milestones: Mapped[list] = mapped_column(JSONB, default=list)
    documents: Mapped[list] = mapped_column(JSONB, default=list)
    note: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
