"""purchase_allocation_items: source_item_id（入库单明细行级归属）

入库单明细反填 SKU 分配时记录来源明细行 ID，用于行级占用排他：
同一行入库明细只能归属一个采购单，防止拆分单/共用入库单把同一行
明细重复反填到多个订单（20260501002 重复带入 20251129002 案例）。

Revision ID: a9e5f7c1d3b6
Revises: b3d7f1a9c2e4
"""

from alembic import op
import sqlalchemy as sa


revision = "a9e5f7c1d3b6"
down_revision = "b3d7f1a9c2e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "purchase_allocation_items",
        sa.Column(
            "source_item_id",
            sa.BigInteger(),
            nullable=True,
            comment="来源入库单明细行 ID（jackyun_goods_document_items.id）；人工行/历史行为空",
        ),
    )
    op.create_index(
        "ix_purchase_allocation_items_source_item_id",
        "purchase_allocation_items",
        ["source_item_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_purchase_allocation_items_source_item_id", table_name="purchase_allocation_items")
    op.drop_column("purchase_allocation_items", "source_item_id")
