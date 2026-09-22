"""1688 订单导出文件与订单明细的本地副本。"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin


class Alibaba1688FileImport(Base, PkMixin, TimestampMixin):
    """一次 1688 官方订单导出文件导入。"""

    __tablename__ = "alibaba1688_file_imports"
    __table_args__ = (
        UniqueConstraint("sha256", name="uq_alibaba1688_file_import_sha256"),
    )

    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    mime: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(32), default="parsed", index=True)
    sheet_name: Mapped[str] = mapped_column(String(256), default="")
    headers: Mapped[list] = mapped_column(JSONB, default=list)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    imported_order_count: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str] = mapped_column(Text, default="")
    uploader: Mapped[str] = mapped_column(String(64), default="system")
    # 导入生命周期：draft=解析完毕待人工确认，active=确认后出现在业务页，deleted=软删除（可恢复）。
    lifecycle: Mapped[str] = mapped_column(String(16), default="active", index=True)
    lifecycle_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Alibaba1688Order(Base, PkMixin, TimestampMixin):
    """1688 订单标准字段；未确认的字段继续保存在 ``raw_payload``。"""

    __tablename__ = "alibaba1688_orders"
    __table_args__ = (
        UniqueConstraint("external_order_id", name="uq_alibaba1688_order_external_id"),
    )

    external_order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    buyer_company_name: Mapped[str] = mapped_column(String(256), default="")
    buyer_member_name: Mapped[str] = mapped_column(String(128), default="")
    seller_company_name: Mapped[str] = mapped_column(String(256), default="")
    supplier_partner_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("business_partners.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # 采购订单的计划入库仓库；实际入库完成后，以入库单上的仓库为准。
    warehouse_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("warehouses.id"), nullable=True, index=True,
    )
    seller_member_name: Mapped[str] = mapped_column(String(128), default="")
    goods_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    freight: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    discount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    actual_payment: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    order_status: Mapped[str] = mapped_column(String(64), default="")
    order_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pay_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 1688 买家订单备注：自动识别其中的吉客云入库单号，但原文仍保留在 raw_payload。
    order_remark: Mapped[str] = mapped_column(Text, default="", server_default="")
    raw_payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    import_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    # 行级状态：active=保留；deleted=用户在明细核对中删除的行（可恢复，不进业务查询）。
    row_status: Mapped[str] = mapped_column(String(16), default="active", server_default="active", index=True)


# 兼容早期 API/页面的类名；实际仍只使用上面的规范表。
Alibaba1688Import = Alibaba1688FileImport
