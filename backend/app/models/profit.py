from decimal import Decimal
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin

MONEY = Numeric(18, 4)

"""利润中心（规格 9）：分层计算，成本缺失不显示假装精确的利润。"""


class CostSnapshot(Base, PkMixin, TimestampMixin):
    """成本优先级：实际采购结算 > 采购订单成本 > SKU默认成本 > 暂估（规格 9）。"""

    __tablename__ = "cost_snapshots"
    __table_args__ = (UniqueConstraint("sku_id", "period_year", "period_month", "source", name="uq_cost_sku_period_src"),)

    sku_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    period_year: Mapped[int] = mapped_column(BigInteger, nullable=False)
    period_month: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actual_cost: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    purch_order_cost: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    default_cost: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    estimated_cost: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="estimated")  # settlement/order/default/estimated
    version: Mapped[int] = mapped_column(BigInteger, default=1)


class AllocatedExpense(Base, PkMixin, TimestampMixin):
    __tablename__ = "allocated_expenses"

    period_year: Mapped[int] = mapped_column(BigInteger, index=True)
    period_month: Mapped[int] = mapped_column(BigInteger, index=True)
    expense_type: Mapped[str] = mapped_column(String(32), default="")  # platform_fee/ad/logistics/other
    dimension: Mapped[dict] = mapped_column(JSONB, default=dict)  # {"platform":..., "store":..., "sku":...}
    amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="")


class ProfitSnapshot(Base, PkMixin, TimestampMixin):
    """结果保存 snapshot：period + formula_version + data_version + calculated_at（规格 9）。"""

    __tablename__ = "profit_snapshots"

    period_year: Mapped[int] = mapped_column(BigInteger, index=True)
    period_month: Mapped[int] = mapped_column(BigInteger, index=True)
    dimension: Mapped[dict] = mapped_column(JSONB, default=dict)  # company/platform/store/spu/sku
    net_sales: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    goods_cost: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    gross_profit: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    platform_fee: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    ad_fee: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    logistics_fee: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    other_fee: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    contribution_profit: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    formula_version: Mapped[str] = mapped_column(String(32), default="v1")
    data_version: Mapped[str] = mapped_column(String(32), default="")
    cost_missing: Mapped[bool] = mapped_column(Boolean, default=False)
    calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
