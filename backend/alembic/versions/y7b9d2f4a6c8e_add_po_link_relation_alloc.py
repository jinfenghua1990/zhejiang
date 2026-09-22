"""jackyun purchase order links: relation kind + alloc amount for merge/split

Revision ID: y7b9d2f4a6c8e
Revises: x6a8c1e3f5b7d
"""

from alembic import op
import sqlalchemy as sa


revision = "y7b9d2f4a6c8e"
down_revision = "x6a8c1e3f5b7d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jackyun_purchase_order_links",
                  sa.Column("relation_kind", sa.String(16), server_default="", nullable=False,
                            comment="'' 普通 / merged 合并（多1688单共1采购单）/ split 拆分（1单分多采购单）"))
    op.add_column("jackyun_purchase_order_links",
                  sa.Column("alloc_amount", sa.Numeric(18, 4), nullable=True,
                            comment="本订单在该吉客云采购单中的分摊金额（合并/拆分场景必填用于金额闭环）"))
    op.add_column("jackyun_purchase_order_links",
                  sa.Column("note", sa.String(256), server_default="", nullable=False))


def downgrade() -> None:
    op.drop_column("jackyun_purchase_order_links", "note")
    op.drop_column("jackyun_purchase_order_links", "alloc_amount")
    op.drop_column("jackyun_purchase_order_links", "relation_kind")
