"""吉客云 MCP 的业务副本模型。

这些表只保存已经从真实响应中确认的维度，``raw`` 保留完整原始记录，
方便接口字段变化或客户端导出文件补齐时重新映射。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin

MONEY = Numeric(18, 4)
QUANTITY = Numeric(18, 4)

class JackyunPurchaseSettlement(Base, PkMixin, TimestampMixin):
    """吉客云采购结算单主档（``settNo`` 为外部稳定编号）。"""

    __tablename__ = "jackyun_purchase_settlements"

    supplier_partner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("business_partners.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    settlement_no: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    settlement_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    supplier_name: Mapped[str] = mapped_column(String(256), default="", index=True)
    company_name: Mapped[str] = mapped_column(String(256), default="")
    total_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    settlement_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    settlement_type: Mapped[str] = mapped_column(String(64), default="")
    purchase_fee: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    paid: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="", index=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class JackyunPurchaseReturn(Base, PkMixin, TimestampMixin):
    """吉客云采购退货单主档；未知字段继续留在 ``raw``。"""

    __tablename__ = "jackyun_purchase_returns"

    supplier_partner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("business_partners.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    return_no: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    purchase_no: Mapped[str] = mapped_column(String(128), default="", index=True)
    supplier_name: Mapped[str] = mapped_column(String(256), default="", index=True)
    warehouse_code: Mapped[str] = mapped_column(String(64), default="", index=True)
    warehouse_name: Mapped[str] = mapped_column(String(256), default="")
    created_at_src: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    returned_at_src: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    return_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="", index=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class JackyunGoodsDocument(Base, PkMixin, TimestampMixin):
    """入库/出库单主档；同一单号按文档类型独立幂等。"""

    __tablename__ = "jackyun_goods_documents"
    __table_args__ = (UniqueConstraint("document_type", "goodsdoc_no", name="uq_jackyun_goods_document_type_no"),)

    document_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # inbound/outbound
    goodsdoc_no: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    document_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    warehouse_code: Mapped[str] = mapped_column(String(64), default="", index=True)
    warehouse_name: Mapped[str] = mapped_column(String(256), default="")
    company_name: Mapped[str] = mapped_column(String(256), default="")
    # 供应商名称：吉客云入库单接口有该字段，接口受限时留空，恢复后由同步逻辑从 raw 补齐
    supplier_name: Mapped[str] = mapped_column(String(256), default="", index=True)
    supplier_partner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("business_partners.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    total_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    # 金额维度：文件导入通道补充（API 接口无此字段时从 Excel 导入）
    total_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    total_fee: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class JackyunGoodsDocumentItem(Base, PkMixin):
    """入库/出库单明细；``line_no`` 保证同一单重复同步时可安全重建。"""

    __tablename__ = "jackyun_goods_document_items"
    __table_args__ = (
        UniqueConstraint("document_id", "line_no", name="uq_jackyun_goods_document_item_line"),
        Index("ix_jgd_items_matched_sku_id", "matched_sku_id"),
        Index("ix_jgd_items_match_status", "match_status"),
    )

    document_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    goods_no: Mapped[str] = mapped_column(String(128), default="", index=True)
    sku_barcode: Mapped[str] = mapped_column(String(128), default="", index=True)
    goods_name: Mapped[str] = mapped_column(String(512), default="")
    quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    unit_name: Mapped[str] = mapped_column(String(32), default="")
    # 以下字段来自吉客云客户端导出文件（API 接口 selectFields 不含这些维度），
    # 文件导入通道写入；API 同步时保持为空。
    spec: Mapped[str] = mapped_column(String(128), default="")
    apply_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    remain_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    return_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    unit_price_tax: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    unit_price_notax: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    amount_tax: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    amount_notax: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    # 批次效期（食品追溯强需求）
    batch_no: Mapped[str] = mapped_column(String(128), default="", index=True)
    production_lot: Mapped[str] = mapped_column(String(128), default="")
    production_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    shelf_life: Mapped[str] = mapped_column(String(32), default="")
    shelf_life_unit: Mapped[str] = mapped_column(String(16), default="")
    manufacturer: Mapped[str] = mapped_column(String(256), default="")
    approval_no: Mapped[str] = mapped_column(String(128), default="")
    goods_status: Mapped[str] = mapped_column(String(64), default="")
    # 货品档案自动匹配（sku_matching_service 写入）
    matched_sku_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # auto=goodsNo 命中；price_ok=金额校验通过；manual=人工指定（自动扫描不覆盖）
    # missing=档案无此货品；price_mismatch=命中但金额异常
    match_status: Mapped[str] = mapped_column(String(16), default="")
    match_note: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class JackyunStockAllocation(Base, PkMixin, TimestampMixin):
    """吉客云仓间调拨单主档。"""

    __tablename__ = "jackyun_stock_allocations"

    allocate_no: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    created_at_src: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    out_warehouse_code: Mapped[str] = mapped_column(String(64), default="", index=True)
    out_warehouse_name: Mapped[str] = mapped_column(String(256), default="")
    in_warehouse_code: Mapped[str] = mapped_column(String(64), default="", index=True)
    in_warehouse_name: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(64), default="", index=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class JackyunShopOrder(Base, PkMixin, TimestampMixin):
    """吉客云网店订单/发货侧主档（``tradeOnline.tradeNo``）。"""

    __tablename__ = "jackyun_shop_orders"

    shop_order_no: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    source_trade_no: Mapped[str] = mapped_column(String(128), default="", index=True)
    shop_name: Mapped[str] = mapped_column(String(256), default="", index=True)
    logistic_no: Mapped[str] = mapped_column(String(128), default="", index=True)
    api_type: Mapped[str] = mapped_column(String(64), default="")
    created_at_src: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    paid_at_src: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    goods_count: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    payment: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="", index=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class JackyunShopOrderItem(Base, PkMixin):
    """吉客云网店订单商品明细；订单头刷新时按行重建，保持幂等。"""

    __tablename__ = "jackyun_shop_order_items"
    __table_args__ = (UniqueConstraint("order_id", "line_no", name="uq_jackyun_shop_order_item_line"),)

    order_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    plat_goods_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    goods_barcode: Mapped[str] = mapped_column(String(128), default="", index=True)
    goods_name: Mapped[str] = mapped_column(String(512), default="")
    quantity: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
