"""耗材自有库存体系：条码/工厂库存/在途/流水前后快照/收货位置

正品库存继续归吉客云；耗材库存由本平台维护，拆分 自有仓/工厂/在途 三个口径，
流水记录操作前后库存快照，收货支持选择到货位置（自有仓/工厂）。

Revision ID: d2e4f6a8b0c1
Revises: a9e5f7c1d3b6
"""

from alembic import op
import sqlalchemy as sa


revision = "d2e4f6a8b0c1"
down_revision = "a9e5f7c1d3b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "consumables",
        sa.Column("barcode", sa.String(128), nullable=False, server_default="", comment="条形码（可与正品相同，不作唯一键）"),
    )
    op.add_column(
        "consumables",
        sa.Column("factory_qty", sa.Numeric(18, 4), nullable=False, server_default="0", comment="工厂库存"),
    )
    op.add_column(
        "consumables",
        sa.Column("transit_qty", sa.Numeric(18, 4), nullable=False, server_default="0", comment="在途库存（发往工厂未收货）"),
    )
    op.add_column(
        "consumable_transactions",
        sa.Column("location", sa.String(16), nullable=True, comment="发生位置：own=自有仓 / factory=工厂"),
    )
    op.add_column(
        "consumable_transactions",
        sa.Column("stock_before", sa.Numeric(18, 4), nullable=True, comment="操作前自有仓库存"),
    )
    op.add_column(
        "consumable_transactions",
        sa.Column("stock_after", sa.Numeric(18, 4), nullable=True, comment="操作后自有仓库存"),
    )
    op.add_column(
        "consumable_transactions",
        sa.Column("factory_before", sa.Numeric(18, 4), nullable=True, comment="操作前工厂库存"),
    )
    op.add_column(
        "consumable_transactions",
        sa.Column("factory_after", sa.Numeric(18, 4), nullable=True, comment="操作后工厂库存"),
    )
    op.add_column(
        "consumable_receipts",
        sa.Column("location", sa.String(16), nullable=False, server_default="own", comment="到货位置：own=自有仓 / factory=工厂"),
    )


def downgrade() -> None:
    op.drop_column("consumable_receipts", "location")
    op.drop_column("consumable_transactions", "factory_after")
    op.drop_column("consumable_transactions", "factory_before")
    op.drop_column("consumable_transactions", "stock_after")
    op.drop_column("consumable_transactions", "stock_before")
    op.drop_column("consumable_transactions", "location")
    op.drop_column("consumables", "transit_qty")
    op.drop_column("consumables", "factory_qty")
    op.drop_column("consumables", "barcode")
