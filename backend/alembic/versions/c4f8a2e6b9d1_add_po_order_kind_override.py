"""external_purchase_orders: order_kind_override（订单类型人工覆盖）

耗材档案 Excel 的「采购订货号」可能填错（例：5092031449423821020 实为
咖啡生豆代烘焙采购，却被「臻选·10种味分享装」外包装行引用，被自动判定
为耗材）。增加人工覆盖字段：goods=正品 / consumable=耗材 / 空=自动判定。

Revision ID: c4f8a2e6b9d1
Revises: d2e4f6a8b0c1
"""

from alembic import op
import sqlalchemy as sa


revision = "c4f8a2e6b9d1"
down_revision = "d2e4f6a8b0c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "external_purchase_orders",
        sa.Column(
            "order_kind_override",
            sa.String(length=16),
            nullable=False,
            server_default="",
            comment="订单类型人工覆盖：goods=正品 / consumable=耗材；空=按自动判定",
        ),
    )


def downgrade() -> None:
    op.drop_column("external_purchase_orders", "order_kind_override")
