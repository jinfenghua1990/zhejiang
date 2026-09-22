from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin


QUANTITY = Numeric(18, 4)


class InventoryStocktakeTask(Base, PkMixin, TimestampMixin):
    """库存盘点任务。

    一个任务固定一个仓库；scope=all 表示该仓库内所选类型全部盘点，
    scope=partial 表示只盘点人工选择的货品/耗材。
    """

    __tablename__ = "inventory_stocktake_tasks"
    __table_args__ = (UniqueConstraint("number", name="uq_inventory_stocktake_tasks_number"),)

    number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    warehouse_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    item_kinds: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    search_text: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    category_filter: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="system")
    confirmed_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InventoryStocktakeItem(Base, PkMixin, TimestampMixin):
    """盘点任务行；completed 任务中的 goods 差异即正品库存调整事实。"""

    __tablename__ = "inventory_stocktake_items"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "item_kind", "ref_id", "warehouse_id",
            name="uq_inventory_stocktake_task_item",
        ),
    )

    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("inventory_stocktake_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    ref_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    warehouse_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    book_qty: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False, default=Decimal("0"))
    actual_qty: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    difference_qty: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
