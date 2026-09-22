"""税务发票清单的原件、标准台账和业务关联。"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin

MONEY = Numeric(18, 4)


class TaxInvoiceImport(Base, PkMixin, TimestampMixin):
    """一次官方税务清单导入；原文件按 SHA256 幂等保存。"""

    __tablename__ = "tax_invoice_imports"
    __table_args__ = (UniqueConstraint("sha256", name="uq_tax_invoice_import_sha256"),)

    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    mime: Mapped[str] = mapped_column(String(128), default="")
    source_system: Mapped[str] = mapped_column(String(64), default="tax_export", index=True)
    period_year: Mapped[int] = mapped_column(Integer, default=0, index=True)
    period_month: Mapped[int] = mapped_column(Integer, default=0, index=True)
    sheet_name: Mapped[str] = mapped_column(String(256), default="")
    headers: Mapped[list] = mapped_column(JSONB, default=list)
    mapping: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="parsed", index=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    recognized_row_count: Mapped[int] = mapped_column(Integer, default=0)
    matched_row_count: Mapped[int] = mapped_column(Integer, default=0)
    needs_review_count: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str] = mapped_column(Text, default="")
    lifecycle: Mapped[str] = mapped_column(String(16), default="active", index=True)
    lifecycle_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    uploader: Mapped[str] = mapped_column(String(64), default="system")
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaxInvoiceImportRecord(Base, PkMixin):
    """导入批次中的原始行；即使无法识别，也不能丢失。"""

    __tablename__ = "tax_invoice_import_records"
    __table_args__ = (UniqueConstraint("import_id", "row_index", name="uq_tax_invoice_import_record"),)

    import_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    recognition_status: Mapped[str] = mapped_column(String(32), default="needs_review", index=True)
    invoice_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    error_summary: Mapped[str] = mapped_column(Text, default="")
    row_status: Mapped[str] = mapped_column(String(16), default="active", server_default="active", index=True)


class TaxInvoice(Base, PkMixin, TimestampMixin):
    """标准税务发票台账；同一发票代码+号码跨批次只保留一条。"""

    __tablename__ = "tax_invoices"
    __table_args__ = (UniqueConstraint("invoice_key", name="uq_tax_invoice_key"),)

    invoice_key: Mapped[str] = mapped_column(String(256), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    invoice_code: Mapped[str] = mapped_column(String(64), default="", index=True)
    invoice_number: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    invoice_type: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    issue_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    seller_name: Mapped[str] = mapped_column(String(256), default="", index=True)
    seller_tax_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    seller_partner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("business_partners.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    buyer_name: Mapped[str] = mapped_column(String(256), default="", index=True)
    buyer_tax_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    buyer_partner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("business_partners.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    amount_excl_tax: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    tax_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    total_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    source_system: Mapped[str] = mapped_column(String(64), default="tax_export", index=True)
    source_import_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    source_row_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    match_status: Mapped[str] = mapped_column(String(32), default="unmatched", index=True)
    match_note: Mapped[str] = mapped_column(Text, default="")
    # 进项发票的业务处理结论：待判断、需处理、暂不处理。
    processing_status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", index=True
    )
    # 进项发票 v2 分类（与 processing_status 合并的单一事实字段）：
    # goods/platform_fee/operating_other=计入运营成本；reimburse_advance/reimburse_operating=计入报销成本；
    # excluded=不计入任何报销运营；空=待判断（未分类）。
    category: Mapped[str] = mapped_column(String(32), default="", server_default="", index=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    verified_month: Mapped[str] = mapped_column(String(16), default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 进项发票人工付款补充字段：personal=个人垫付；platform_auto_debit=平台自动扣款货款；空=未设置。
    # corporate / mixed 不直接存库，必须由已确认 bank_transaction 付款证据动态派生。
    # 销项发票不适用；历史错误值在序列化时忽略。
    payment_method: Mapped[str] = mapped_column(String(32), default="", server_default="", index=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class TaxInvoiceLink(Base, PkMixin, TimestampMixin):
    """发票与采购/销售业务单据的可审计关联。"""

    __tablename__ = "tax_invoice_links"
    __table_args__ = (
        UniqueConstraint("invoice_id", "target_type", "target_id", name="uq_tax_invoice_link_target"),
    )

    invoice_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    allocated_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    match_method: Mapped[str] = mapped_column(String(32), default="manual")
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    note: Mapped[str] = mapped_column(Text, default="")


class TaxAccountingCategoryRule(Base, PkMixin, TimestampMixin):
    """用户可维护的财务大类规则，例如 *软饮料*咖啡。

    tax_code 是货品档案可引用的开票税收分类编码，避免财务规则和货品档案各自
    维护一份互不相认的代码。
    """

    __tablename__ = "tax_accounting_category_rules"
    __table_args__ = (
        UniqueConstraint("category_name", "item_name", name="uq_tax_accounting_category_rule"),
    )

    category_name: Mapped[str] = mapped_column(String(128), nullable=False)
    item_name: Mapped[str] = mapped_column(String(256), nullable=False)
    tax_code: Mapped[str] = mapped_column(String(32), default="", server_default="", nullable=False)
    match_keyword: Mapped[str] = mapped_column(String(256), default="")
    match_mode: Mapped[str] = mapped_column(String(16), default="contains")
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    updated_by: Mapped[str] = mapped_column(String(64), default="system")
