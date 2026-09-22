from decimal import Decimal
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin

MONEY = Numeric(18, 4)

"""财务资料中心：原始资料版本化归档 + 月度销售汇总模板 + 不可覆盖交付包。"""


class ArchiveFile(Base, PkMixin, TimestampMixin):
    """原始/系统生成文件元数据。同名禁止静默覆盖 → version 递增。"""

    __tablename__ = "archive_files"
    __table_args__ = (
        UniqueConstraint("company", "period_year", "period_month", "category", "original_name", "version",
                         name="uq_archive_file_version"),
    )

    company: Mapped[str] = mapped_column(String(256), default="浙江柴本网络科技有限公司", index=True)
    category: Mapped[str] = mapped_column(String(32), default="other")
    # bank/jackyun/invoice/sales_summary/purchase_inbound/sales_query/other
    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    mime: Mapped[str] = mapped_column(String(128), default="")
    sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    period_year: Mapped[int] = mapped_column(BigInteger, default=0, index=True)
    period_month: Mapped[int] = mapped_column(BigInteger, default=0, index=True)
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    uploader: Mapped[str] = mapped_column(String(64), default="system")
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MonthlyFinancePeriod(Base, PkMixin, TimestampMixin):
    __tablename__ = "monthly_finance_periods"
    __table_args__ = (UniqueConstraint("company", "period_year", "period_month", name="uq_finance_period"),)

    company: Mapped[str] = mapped_column(String(256), default="浙江柴本网络科技有限公司")
    period_year: Mapped[int] = mapped_column(BigInteger, nullable=False)
    period_month: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="INCOMPLETE")  # INCOMPLETE/READY/PACKAGED/SENT/ERROR
    required_types: Mapped[dict] = mapped_column(JSONB, default=dict)
    missing_summary: Mapped[dict] = mapped_column(JSONB, default=dict)


class MonthlyIntakeSource(Base, PkMixin, TimestampMixin):
    """月度业务源文件的当前版本与导入结果。

    原始文件仍由 ``ArchiveFile`` 负责不可覆盖的版本化归档；本表只保存
    每个账期、每种源文件的当前指针和处理状态，保证用户下个月回来时
    能看见「采购入库单 / 销售单查询」是否真正导入，而不是只看到文件存在。
    """

    __tablename__ = "monthly_intake_sources"
    __table_args__ = (
        UniqueConstraint(
            "company", "period_year", "period_month", "source_type",
            name="uq_monthly_intake_source",
        ),
        Index(
            "ix_monthly_intake_sources_period",
            "company", "period_year", "period_month",
        ),
    )

    company: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    period_year: Mapped[int] = mapped_column(BigInteger, nullable=False)
    period_month: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # purchase_inbound / sales_query
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    archive_file_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    # MISSING/IMPORTING/IMPORTED/REVIEW/ERROR
    status: Mapped[str] = mapped_column(String(16), default="MISSING", index=True)
    report_type: Mapped[str] = mapped_column(String(32), default="")
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_summary: Mapped[str] = mapped_column(Text, default="")
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FinanceSalesReportTemplate(Base, PkMixin, TimestampMixin):
    """每月销售汇总模板；后台任务直接读取，不能依赖浏览器 LocalStorage。"""

    __tablename__ = "finance_sales_report_templates"
    __table_args__ = (
        UniqueConstraint("company", "name", name="uq_finance_sales_report_template_company_name"),
        CheckConstraint("send_day >= 1 AND send_day <= 28", name="ck_finance_sales_report_send_day"),
        CheckConstraint("send_hour >= 0 AND send_hour <= 23", name="ck_finance_sales_report_send_hour"),
    )

    company: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="默认财务月报")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # [{"key":"platform","label":"平台","enabled":true}, ...]，数组顺序就是 Excel 列顺序。
    fields: Mapped[list] = mapped_column(JSONB, default=list)
    # 业务口径，例如 valid_order_mode / refund_mode / timezone。
    rules: Mapped[dict] = mapped_column(JSONB, default=dict)
    to_addrs: Mapped[list] = mapped_column(JSONB, default=list)
    cc_addrs: Mapped[list] = mapped_column(JSONB, default=list)
    auto_send: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    send_day: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    send_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=10)


class FinanceUnbilledAdjustment(Base, PkMixin, TimestampMixin):
    """无票收入明细的月度选择版本；每次保存都新增版本，不覆盖历史口径。"""

    __tablename__ = "finance_unbilled_adjustments"
    __table_args__ = (
        UniqueConstraint(
            "company", "period_year", "period_month", "version",
            name="uq_finance_unbilled_adjustment_version",
        ),
        Index(
            "ix_finance_unbilled_adjustments_period",
            "company", "period_year", "period_month",
        ),
    )

    company: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    period_year: Mapped[int] = mapped_column(BigInteger, nullable=False)
    period_month: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 明细键为 taxCode + 分隔符 + product，保存的是本版本保留的明细。
    selected_keys: Mapped[list] = mapped_column(JSONB, default=list)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    note: Mapped[str] = mapped_column(Text, default="")


class FinanceCorporatePaymentAdjustment(Base, PkMixin, TimestampMixin):
    """已收票对公付款发票的月度选择版本；每次保存都新增版本，不覆盖历史口径。"""

    __tablename__ = "finance_corporate_payment_adjustments"
    __table_args__ = (
        UniqueConstraint(
            "company", "period_year", "period_month", "version",
            name="uq_finance_corporate_payment_adjustment_version",
        ),
        Index(
            "ix_finance_corporate_payment_adjustments_period",
            "company", "period_year", "period_month",
        ),
    )

    company: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    period_year: Mapped[int] = mapped_column(BigInteger, nullable=False)
    period_month: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 明细键为进项发票号码，保存的是本版本保留的发票。
    selected_keys: Mapped[list] = mapped_column(JSONB, default=list)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    note: Mapped[str] = mapped_column(Text, default="")


class FinanceDeliveryPackage(Base, PkMixin, TimestampMixin):
    __tablename__ = "finance_delivery_packages"
    __table_args__ = (UniqueConstraint("period_id", "version", name="uq_finance_delivery_package_version"),)

    period_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, default=1)  # V1/V2…，V1 发出后不可变
    zip_path: Mapped[str] = mapped_column(Text, default="")
    zip_sha256: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="PACKAGED")  # PACKAGED/SENT/ERROR
    created_at_src: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FinanceDeliveryFile(Base, PkMixin):
    __tablename__ = "finance_delivery_files"

    package_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("finance_delivery_packages.id", name="fk_fdf_package", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    archive_file_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("archive_files.id", name="fk_fdf_archive", ondelete="CASCADE"),
        index=True, nullable=False,
    )


class EmailDeliveryLog(Base, PkMixin, TimestampMixin):
    """一个账期+版本只允许一条“首次成功发送”；再次发送标记 RESENT。"""

    __tablename__ = "email_delivery_logs"
    __table_args__ = (
        Index(
            "uq_email_delivery_first_sent",
            "package_id",
            unique=True,
            postgresql_where=text("kind = 'first' AND status = 'sent'"),
        ),
    )

    package_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="first")  # first/resent
    to_addrs: Mapped[dict] = mapped_column(JSONB, default=list)
    cc_addrs: Mapped[dict] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(16), default="sent")  # sent/failed
    message_id: Mapped[str] = mapped_column(String(256), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClosingVersion(Base, PkMixin, TimestampMixin):
    __tablename__ = "closing_versions"
    __table_args__ = (UniqueConstraint("period_id", "version", name="uq_closing_version"),)

    period_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)



class FinanceLegalEntity(Base, PkMixin, TimestampMixin):
    """财务主体主档。财务中心的第一维度。"""

    __tablename__ = "finance_legal_entities"
    __table_args__ = (
        UniqueConstraint("code", name="uq_finance_legal_entities_code"),
        UniqueConstraint("name", name="uq_finance_legal_entities_name"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    country_code: Mapped[str] = mapped_column(String(8), default="CN")
    base_currency: Mapped[str] = mapped_column(String(8), default="CNY")
    tax_id: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    business_scopes: Mapped[list] = mapped_column(JSONB, default=lambda: ["domestic", "foreign_trade"])
    note: Mapped[str] = mapped_column(Text, default="")


class FinanceEntry(Base, PkMixin, TimestampMixin):
    """统一财务事项池。业务事实先归集到这里，再做收支、税务、利润和月结。"""

    __tablename__ = "finance_entries"
    __table_args__ = (
        UniqueConstraint(
            "legal_entity_id",
            "source_type",
            "source_id",
            "category",
            "value_type",
            name="uq_finance_entry_source_category_value",
        ),
        Index(
            "ix_finance_entries_period_scope",
            "legal_entity_id", "accounting_year", "accounting_month", "business_scope",
        ),
    )

    legal_entity_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("finance_legal_entities.id", name="fk_finance_entries_legal_entity", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    business_scope: Mapped[str] = mapped_column(String(24), default="domestic", index=True)
    source_type: Mapped[str] = mapped_column(String(48), default="manual", index=True)
    source_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    source_no: Mapped[str] = mapped_column(String(128), default="", index=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(24), default="expense", index=True)
    cash_effect: Mapped[bool] = mapped_column(Boolean, default=True)
    profit_effect: Mapped[bool] = mapped_column(Boolean, default=True)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    amount: Mapped[Decimal] = mapped_column(MONEY, default=0)
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, default=0)
    value_type: Mapped[str] = mapped_column(String(16), default="actual", index=True)
    settlement_status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    invoice_status: Mapped[str] = mapped_column(String(24), default="unknown", index=True)
    accounting_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    accounting_month: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
