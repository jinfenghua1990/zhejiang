from decimal import Decimal
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin

MONEY = Numeric(18, 4)


class SalesOrder(Base, PkMixin, TimestampMixin):
    __tablename__ = "sales_orders"
    __table_args__ = (
        Index("ix_sales_orders_identity_keys_gin", "identity_keys", postgresql_using="gin"),
    )

    order_no: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    # 吉客云三通道统一订单入口写入；旧订单使用 legacy，避免伪造来源。
    source_provider: Mapped[str] = mapped_column(String(32), default="legacy", index=True)
    source_order_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    identity_keys: Mapped[list] = mapped_column(JSONB, default=list)
    source_history: Mapped[list] = mapped_column(JSONB, default=list)
    store_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    customer_partner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("business_partners.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    platform: Mapped[str] = mapped_column(String(64), default="", index=True)
    order_type: Mapped[str] = mapped_column(String(64), default="")
    order_status: Mapped[str] = mapped_column(String(64), default="", index=True)
    pay_status: Mapped[str] = mapped_column(String(64), default="")
    buyer_note: Mapped[str] = mapped_column(Text, default="")
    order_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    paid_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class SalesOrderItem(Base, PkMixin):
    __tablename__ = "sales_order_items"

    order_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    sku_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    sku_code: Mapped[str] = mapped_column(String(128), default="", index=True)
    goods_name: Mapped[str] = mapped_column(String(512), default="")
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    discount_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class AftersalesOrder(Base, PkMixin, TimestampMixin):
    __tablename__ = "aftersales_orders"

    aftersale_no: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    order_no: Mapped[str] = mapped_column(String(128), default="", index=True)
    type: Mapped[str] = mapped_column(String(32), default="")  # refund/return/exchange
    status: Mapped[str] = mapped_column(String(64), default="", index=True)
    refund_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at_src: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class Shipment(Base, PkMixin):
    __tablename__ = "shipments"

    order_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    shipment_no: Mapped[str] = mapped_column(String(128), default="", index=True)
    carrier: Mapped[str] = mapped_column(String(128), default="")
    tracking_no: Mapped[str] = mapped_column(String(128), default="", index=True)
    status: Mapped[str] = mapped_column(String(32), default="")
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
