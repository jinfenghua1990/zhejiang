"""add sku matching fields

Revision ID: r9d4e7f1a3b6
Revises: q8c3f6b9d2e5
Create Date: 2026-09-04 00:30
"""

from alembic import op
import sqlalchemy as sa


revision = "r9d4e7f1a3b6"
down_revision = "q8c3f6b9d2e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jackyun_goods_document_items", sa.Column("matched_sku_id", sa.BigInteger(), nullable=True))
    op.add_column("jackyun_goods_document_items", sa.Column("match_status", sa.String(length=16), nullable=False, server_default=""))
    op.add_column("jackyun_goods_document_items", sa.Column("match_note", sa.Text(), nullable=False, server_default=""))
    op.create_index("ix_jgd_items_matched_sku_id", "jackyun_goods_document_items", ["matched_sku_id"])
    op.create_index("ix_jgd_items_match_status", "jackyun_goods_document_items", ["match_status"])

    op.add_column("purchase_allocation_items", sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"))
    op.add_column("purchase_allocation_items", sa.Column("match_confidence", sa.Numeric(5, 4), nullable=True))


def downgrade() -> None:
    op.drop_column("purchase_allocation_items", "match_confidence")
    op.drop_column("purchase_allocation_items", "source")
    op.drop_index("ix_jgd_items_match_status", table_name="jackyun_goods_document_items")
    op.drop_index("ix_jgd_items_matched_sku_id", table_name="jackyun_goods_document_items")
    op.drop_column("jackyun_goods_document_items", "match_note")
    op.drop_column("jackyun_goods_document_items", "match_status")
    op.drop_column("jackyun_goods_document_items", "matched_sku_id")
