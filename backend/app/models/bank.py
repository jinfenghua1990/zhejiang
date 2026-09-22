from decimal import Decimal
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin

MONEY = Numeric(18, 4)

"""浙江农信只做文件导入（规格 1.3）：不做 API 直联、不做网页 RPA。原始文件长期保存不覆盖。"""


class BankAccount(Base, PkMixin, TimestampMixin):
    __tablename__ = "bank_accounts"

    account_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # 面向系统内部的稳定编号；account_no 始终保存真实银行账号。
    internal_code: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    account_name: Mapped[str] = mapped_column(String(256), default="")
    bank_name: Mapped[str] = mapped_column(String(256), default="")
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    opening_balance: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)


class BankImportBatch(Base, PkMixin, TimestampMixin):
    __tablename__ = "bank_import_batches"

    source: Mapped[str] = mapped_column(String(32), default="zjrc")  # 浙江农信
    file_name: Mapped[str] = mapped_column(Text, default="")
    archive_file_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    period_year: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    period_month: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    row_count: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(32), default="done")


class BankTransaction(Base, PkMixin, TimestampMixin):
    __tablename__ = "bank_transactions"
    __table_args__ = (UniqueConstraint("fingerprint", name="uq_bank_txn_fingerprint"),)

    account_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    import_batch_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    txn_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    transaction_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    direction: Mapped[str] = mapped_column(String(8), default="in")  # in/out
    amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    counterparty_name: Mapped[str] = mapped_column(String(256), default="", index=True)
    counterparty_account: Mapped[str] = mapped_column(String(128), default="")
    counterparty_partner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("business_partners.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    summary: Mapped[str] = mapped_column(Text, default="")
    serial_no: Mapped[str] = mapped_column(String(128), default="", index=True)
    voucher_no: Mapped[str] = mapped_column(String(128), default="", index=True)
    source_row_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 幂等指纹：账户+日期+金额+流水号（规格 16），流水号为空用可重复 hash fallback
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class BankReceiptFile(Base, PkMixin, TimestampMixin):
    __tablename__ = "bank_receipt_files"

    archive_file_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    company: Mapped[str] = mapped_column(String(256), default="")
    period_year: Mapped[int] = mapped_column(BigInteger, default=0)
    period_month: Mapped[int] = mapped_column(BigInteger, default=0)


class BankTransactionReceiptLink(Base, PkMixin):
    __tablename__ = "bank_transaction_receipt_links"
    __table_args__ = (UniqueConstraint("txn_id", "receipt_file_id", name="uq_txn_receipt"),)

    txn_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    receipt_file_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)


class CounterpartyMappingRule(Base, PkMixin, TimestampMixin):
    """对方户名 → 平台 映射规则；规则变更必须有日志（规格 8.2）。"""

    __tablename__ = "counterparty_mapping_rules"

    match_pattern: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    match_type: Mapped[str] = mapped_column(String(16), default="contains")  # contains/equals
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
