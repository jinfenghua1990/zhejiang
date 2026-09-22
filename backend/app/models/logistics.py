"""快递物流（成本管理）模型（规格：日常预估 → 半年账单 → 实际核销 → 财务利润自动修正）。

- 未出账：财务利润用的是【预估运费】= 有效发货单量 × 默认预估单价
- 已出账：财务利润用的是【实际账单金额】，实际必须替代预估，严禁两者叠加
- 发货单量取吉客云「销售出库单(outbound)」单据数，按发货日期(document_at)归月
- 第一阶段的账单允许只按「账期 + 物流公司 + 总金额」整体核销，不要求运单级明细
"""

from decimal import Decimal
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin

MONEY = Numeric(18, 4)


class LogisticsSetting(Base, PkMixin, TimestampMixin):
    """物流设置项（key-value），当前只有默认预估单价。"""

    __tablename__ = "logistics_settings"
    __table_args__ = (UniqueConstraint("key", name="uq_logistics_settings_key"),)

    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(String(255), default="")


class LogisticsBill(Base, PkMixin, TimestampMixin):
    """物流账单：按账期 + 物流公司一粒。核销后写入实际金额，供财务利润替换预估。"""

    __tablename__ = "logistics_bills"
    __table_args__ = (
        Index("ix_logistics_bills_period_end", "period_end"),
        Index("ix_logistics_bills_status", "status"),
    )

    period_label: Mapped[str] = mapped_column(String(64), default="")  # 如 "2026 H1" / "2026-01 ~ 2026-06"
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    carrier: Mapped[str] = mapped_column(String(128), default="")  # 物流公司
    waybill_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 运单数
    estimated_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 系统预估
    actual_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 实际账单金额
    actual_unit_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 实际单均
    difference: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # 实际 - 预估
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/partial/settled/abnormal
    invoice_status: Mapped[str] = mapped_column(String(16), default="none")  # none/uninvoiced/invoiced
    attachment_name: Mapped[str] = mapped_column(String(255), default="")
    attachment_path: Mapped[str] = mapped_column(String(512), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    matched_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 匹配运单
    unmatched_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 未匹配
    duplicate_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 重复运单
    abnormal_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 异常金额运单
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
