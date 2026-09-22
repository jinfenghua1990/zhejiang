"""自动记账凭证：凭证头 + 借贷分录。

把 finance_entries（扁平财务事项池）按规则自动转成标准记账凭证，
每张凭证的借贷必须平衡（差额 <= 0.01 视为平衡）。
"""
from decimal import Decimal

from sqlalchemy import (
    BigInteger, DateTime, ForeignKey, Index, Integer, Numeric, String, Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin

MONEY = Numeric(18, 4)


class FinanceVoucher(Base, PkMixin, TimestampMixin):
    __tablename__ = "finance_vouchers"
    __table_args__ = (
        UniqueConstraint("legal_entity_id", "voucher_no", name="uq_finance_voucher_no"),
        Index("ix_finance_vouchers_period", "legal_entity_id", "accounting_year", "accounting_month"),
    )

    legal_entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    voucher_no: Mapped[str] = mapped_column(String(64), nullable=False)
    accounting_year: Mapped[int] = mapped_column(Integer, nullable=False)
    accounting_month: Mapped[int] = mapped_column(Integer, nullable=False)
    voucher_date: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), server_default="CNY", nullable=False)
    source: Mapped[str] = mapped_column(String(16), server_default="auto", nullable=False)
    status: Mapped[str] = mapped_column(String(16), server_default="draft", nullable=False)
    note: Mapped[str] = mapped_column(Text, server_default="", nullable=False)
    raw: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)


class FinanceVoucherLine(Base, PkMixin, TimestampMixin):
    __tablename__ = "finance_voucher_lines"
    __table_args__ = (
        Index("ix_finance_voucher_lines_voucher", "voucher_id"),
        Index("ix_finance_voucher_lines_entry", "entry_id"),
    )

    voucher_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("finance_vouchers.id", ondelete="CASCADE"), nullable=False
    )
    entry_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    seq: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    account_code: Mapped[str] = mapped_column(String(32), server_default="", nullable=False)
    account_name: Mapped[str] = mapped_column(String(128), server_default="", nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, server_default="0", nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, server_default="0", nullable=False)
    summary: Mapped[str] = mapped_column(Text, server_default="", nullable=False)
