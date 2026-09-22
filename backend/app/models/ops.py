from decimal import Decimal
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin

MONEY = Numeric(18, 4)

"""期初初始化（规格 11）：允许不平，历史差异进差异池，不篡改历史订单。"""


class OpeningBalance(Base, PkMixin, TimestampMixin):
    __tablename__ = "opening_balances"

    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # platform_receivable / bank / sku_inventory / sku_cost / deposit / frozen / other
    ref: Mapped[str] = mapped_column(String(256), default="", index=True)  # 平台名 / 账户 / SKU
    amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")


class OpeningAdjustment(Base, PkMixin, TimestampMixin):
    __tablename__ = "opening_adjustments"

    opening_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    delta: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64), default="system")


class ExceptionRecord(Base, PkMixin, TimestampMixin):
    """异常中心（规格 12）：所有异常统一处理，处理留痕。"""

    __tablename__ = "exceptions"

    code: Mapped[str] = mapped_column(String(64), default="", index=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), default="medium")  # low/medium/high
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # pending/confirmed/ignored/resolved
    source: Mapped[str] = mapped_column(String(32), default="system")  # system/manual
    ref_table: Mapped[str] = mapped_column(String(64), default="")
    ref_id: Mapped[str] = mapped_column(String(64), default="")
    assignee: Mapped[str] = mapped_column(String(64), default="")
    handled_by: Mapped[str] = mapped_column(String(64), default="")
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
