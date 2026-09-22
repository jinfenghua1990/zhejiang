from decimal import Decimal
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin

MONEY = Numeric(18, 4)

"""吉客云是唯一商品/SKU 主档（规格 1.1）：关联主键用吉客云内部 ID / SKU 编码。"""


class Product(Base, PkMixin, TimestampMixin):
    __tablename__ = "products"

    jackyun_goods_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    goods_code: Mapped[str] = mapped_column(String(128), default="", index=True)
    goods_name: Mapped[str] = mapped_column(String(512), default="", index=True)
    category: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(32), default="active")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class ProductSku(Base, PkMixin, TimestampMixin):
    __tablename__ = "product_skus"
    __table_args__ = (
        UniqueConstraint("jackyun_sku_id", name="uq_sku_jackyun_id"),
        UniqueConstraint("sku_code", name="uq_sku_code"),
    )

    product_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    jackyun_sku_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    sku_code: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    product_type: Mapped[str] = mapped_column(String(16), default="single", nullable=False, index=True)
    sku_name: Mapped[str] = mapped_column(String(512), default="", index=True)
    barcode: Mapped[str] = mapped_column(String(128), default="", index=True)
    unit: Mapped[str] = mapped_column(String(32), default="")
    sale_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    default_cost: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    cost_mode: Mapped[str] = mapped_column(String(16), default="fixed", nullable=False)
    cost_tolerance_pct: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("0.0200"), nullable=False)
    tax_code: Mapped[str] = mapped_column(
        String(32), default="", server_default="", nullable=False,
        comment="税收分类编码（开票用，19 位；兼容旧 10 位简称）",
    )
    tax_category_rule_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("tax_accounting_category_rules.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="active")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class Warehouse(Base, PkMixin, TimestampMixin):
    """统一仓库主档：吉客云真实仓 + 本平台工厂仓共用一张表。

    jackyun_warehouse_id 为空表示本平台自定义仓（例如工厂仓）；有值表示可以直接
    承接吉客云 InventorySnapshot。code/name/role 等字段由用户在设置页灵活维护。
    """

    __tablename__ = "warehouses"

    jackyun_warehouse_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    code: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(256), default="", index=True)
    warehouse_type: Mapped[str] = mapped_column(String(64), default="other", index=True)
    purpose: Mapped[str] = mapped_column(String(16), default="both")
    is_sellable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class InventorySnapshot(Base, PkMixin):
    __tablename__ = "inventory_snapshots"

    sku_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    warehouse_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="jackyun")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class SalesChannel(Base, PkMixin, TimestampMixin):
    __tablename__ = "sales_channels"

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), default="")


class Store(Base, PkMixin, TimestampMixin):
    __tablename__ = "stores"

    jackyun_store_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), default="")
    platform: Mapped[str] = mapped_column(String(64), default="", index=True)
    channel_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
