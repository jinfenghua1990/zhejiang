"""add logistics setting and bills

Revision ID: 464934dfa603
Revises: 4d7509381474
Create Date: 2026-09-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "464934dfa603"
down_revision = "4d7509381474"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "logistics_settings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_logistics_settings_key"),
    )

    op.create_table(
        "logistics_bills",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("period_label", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("carrier", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("waybill_count", sa.BigInteger(), nullable=True),
        sa.Column("estimated_amount", sa.Numeric(18, 4), nullable=True),
        sa.Column("actual_amount", sa.Numeric(18, 4), nullable=True),
        sa.Column("actual_unit_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("difference", sa.Numeric(18, 4), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("invoice_status", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column("attachment_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("attachment_path", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("matched_count", sa.BigInteger(), nullable=True),
        sa.Column("unmatched_count", sa.BigInteger(), nullable=True),
        sa.Column("duplicate_count", sa.BigInteger(), nullable=True),
        sa.Column("abnormal_count", sa.BigInteger(), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_logistics_bills_period_end", "logistics_bills", ["period_end"])
    op.create_index("ix_logistics_bills_status", "logistics_bills", ["status"])


def downgrade() -> None:
    op.drop_index("ix_logistics_bills_status", table_name="logistics_bills")
    op.drop_index("ix_logistics_bills_period_end", table_name="logistics_bills")
    op.drop_table("logistics_bills")
    op.drop_table("logistics_settings")
