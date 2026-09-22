from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin


MONEY = Numeric(18, 4)
QUANTITY = Numeric(18, 4)
UNIT_COST = Numeric(24, 10)


class Consumable(Base, PkMixin, TimestampMixin):
    """外包装等耗材主档；code 是用户自定义耗材编码（不自动生成）。

    库存三口径目前继续兼容：stock_qty=非工厂仓汇总、factory_qty=工厂仓汇总、transit_qty=在途。
    新业务流水同时记录 warehouse_id，后续可平滑升级为完全按仓库核算。
    """

    __tablename__ = "consumables"

    code: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), default="", index=True)
    barcode: Mapped[str] = mapped_column(
        String(128), default="", server_default="", index=True,
        comment="条形码（可与正品相同，不作唯一键）",
    )
    category: Mapped[str] = mapped_column(String(128), default="", index=True)
    unit: Mapped[str] = mapped_column(String(32), default="个")
    purchase_unit_cost: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    purchased_qty: Mapped[Decimal] = mapped_column(QUANTITY, default=Decimal("0"))
    used_qty: Mapped[Decimal] = mapped_column(QUANTITY, default=Decimal("0"))
    stock_qty: Mapped[Decimal] = mapped_column(QUANTITY, default=Decimal("0"))
    factory_qty: Mapped[Decimal] = mapped_column(QUANTITY, default=Decimal("0"), comment="工厂库存")
    transit_qty: Mapped[Decimal] = mapped_column(QUANTITY, default=Decimal("0"), comment="在途库存（发往工厂未收货）")
    min_stock_qty: Mapped[Decimal] = mapped_column(QUANTITY, default=Decimal("0"))
    tax_code: Mapped[str] = mapped_column(
        String(32), default="", server_default="", nullable=False,
        comment="税收分类编码（开票用，19 位；兼容旧 10 位简称）",
    )
    tax_category_rule_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("tax_accounting_category_rules.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class ConsumableSkuMapping(Base, PkMixin, TimestampMixin):
    """货品每销售/采购一个单位消耗多少包装材料。"""

    __tablename__ = "consumable_sku_mappings"
    __table_args__ = (UniqueConstraint("sku_id", "consumable_id", name="uq_consumable_sku_mapping"),)

    sku_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    consumable_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    usage_per_unit: Mapped[Decimal] = mapped_column(QUANTITY, default=Decimal("1"))
    note: Mapped[str] = mapped_column(Text, default="")


class ConsumableTransaction(Base, PkMixin, TimestampMixin):
    """耗材库存流水；所有库存变化必须留痕。

    location 保留 old/factory 兼容口径；warehouse_id 是新的可配置仓库事实引用。
    """

    __tablename__ = "consumable_transactions"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "consumable_id", name="uq_consumable_tx_source"),
        UniqueConstraint("request_key", name="uq_consumable_tx_request"),
    )

    consumable_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    request_key: Mapped[str | None] = mapped_column(String(36), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(UNIT_COST, nullable=True)
    warehouse_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("warehouses.id"), nullable=True, index=True)
    location: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="发生位置：own=自有仓 / factory=工厂")
    source_type: Mapped[str] = mapped_column(String(32), default="manual")
    source_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    stock_before: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True, comment="操作前自有仓库存")
    stock_after: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True, comment="操作后自有仓库存")
    factory_before: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True, comment="操作前工厂库存")
    factory_after: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True, comment="操作后工厂库存")
    note: Mapped[str] = mapped_column(Text, default="")
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class InboundConsumableUsage(Base, PkMixin, TimestampMixin):
    """一张吉客云入库单关联的耗材出库明细。"""

    __tablename__ = "inbound_consumable_usages"
    __table_args__ = (UniqueConstraint("link_id", "consumable_id", name="uq_inbound_consumable_usage"),)

    link_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    inbound_document_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    consumable_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="")
