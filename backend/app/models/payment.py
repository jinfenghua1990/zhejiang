from decimal import Decimal
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin

MONEY = Numeric(18, 4)

"""回款中心（规格 8.2）：应收 vs 实际到账 → 匹配 → 差异。"""


class SettlementRecord(Base, PkMixin, TimestampMixin):
    __tablename__ = "settlement_records"

    platform: Mapped[str] = mapped_column(String(64), index=True)
    store_name: Mapped[str] = mapped_column(String(256), default="")
    period_year: Mapped[int] = mapped_column(BigInteger, default=0, index=True)
    period_month: Mapped[int] = mapped_column(BigInteger, default=0, index=True)
    expected_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    source: Mapped[str] = mapped_column(String(32), default="manual")  # manual/jackyun/file
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)  # open/partial/settled
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class ReconciliationMatch(Base, PkMixin, TimestampMixin):
    """匹配输出 confidence + target + status，不能只靠金额（规格 8.2）。"""

    __tablename__ = "reconciliation_matches"
    __table_args__ = (
        # 一笔银行流水只能确认到一个真实目标，rejected/suggested 历史不受限制。
        Index(
            "uq_reconciliation_matches_confirmed_txn",
            "txn_id",
            unique=True,
            postgresql_where=text("status = 'confirmed'"),
        ),
    )

    txn_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), default="")  # settlement/opening/expense
    target_id: Mapped[int] = mapped_column(BigInteger, default=0, index=True)
    score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    confidence: Mapped[str] = mapped_column(String(16), default="low")  # high/medium/low
    status: Mapped[str] = mapped_column(String(16), default="suggested")  # suggested/confirmed/rejected
    matched_platform: Mapped[str] = mapped_column(String(64), default="")
    matched_by: Mapped[str] = mapped_column(String(32), default="rule")  # rule/manual
    note: Mapped[str] = mapped_column(Text, default="")


class ReceivableSnapshot(Base, PkMixin, TimestampMixin):
    """期初 + 本期发生 − 本期结算 = 期末（规格 11）。"""

    __tablename__ = "receivable_snapshots"
    __table_args__ = (UniqueConstraint("platform", "period_year", "period_month", name="uq_receivable_period"),)

    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    period_year: Mapped[int] = mapped_column(BigInteger, nullable=False)
    period_month: Mapped[int] = mapped_column(BigInteger, nullable=False)
    opening: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    incurred: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    settled: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    closing: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    difference: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
