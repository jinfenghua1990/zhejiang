"""persist normalized shop-order item details

Revision ID: h1f6a8c3d5e7
Revises: g9e2f5b7c1d3
Create Date: 2026-09-02

Shop-order responses include product lines alongside the order header.  Keep
those lines independently queryable while retaining the complete source row
on the parent and item records.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "h1f6a8c3d5e7"
down_revision = "g9e2f5b7c1d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jackyun_shop_order_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("plat_goods_id", sa.String(length=128), nullable=False),
        sa.Column("goods_barcode", sa.String(length=128), nullable=False),
        sa.Column("goods_name", sa.String(length=512), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("unit_price", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("amount", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "line_no", name="uq_jackyun_shop_order_item_line"),
    )
    op.create_index("ix_jackyun_shop_order_items_order_id", "jackyun_shop_order_items", ["order_id"])
    op.create_index("ix_jackyun_shop_order_items_plat_goods_id", "jackyun_shop_order_items", ["plat_goods_id"])
    op.create_index("ix_jackyun_shop_order_items_goods_barcode", "jackyun_shop_order_items", ["goods_barcode"])


def downgrade() -> None:
    op.drop_index("ix_jackyun_shop_order_items_goods_barcode", table_name="jackyun_shop_order_items")
    op.drop_index("ix_jackyun_shop_order_items_plat_goods_id", table_name="jackyun_shop_order_items")
    op.drop_index("ix_jackyun_shop_order_items_order_id", table_name="jackyun_shop_order_items")
    op.drop_table("jackyun_shop_order_items")
