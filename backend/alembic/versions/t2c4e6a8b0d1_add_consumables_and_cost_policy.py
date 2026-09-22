"""add consumable ledger and sku cost policy

Revision ID: t2c4e6a8b0d1
Revises: s1e5f8a2b4c6
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "t2c4e6a8b0d1"
down_revision = "s1e5f8a2b4c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("product_skus", sa.Column("cost_mode", sa.String(length=16), nullable=False, server_default="fixed"))
    op.add_column("product_skus", sa.Column("cost_tolerance_pct", sa.Numeric(5, 4), nullable=False, server_default="0.0200"))

    op.create_table(
        "consumables",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("unit", sa.String(length=32), nullable=False, server_default="个"),
        sa.Column("purchase_unit_cost", sa.Numeric(18, 4), nullable=True),
        sa.Column("stock_qty", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("min_stock_qty", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_consumables_code"),
    )
    op.create_index("ix_consumables_name", "consumables", ["name"])
    op.create_index("ix_consumables_category", "consumables", ["category"])
    op.create_index("ix_consumables_status", "consumables", ["status"])

    op.create_table(
        "consumable_sku_mappings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=False),
        sa.Column("consumable_id", sa.BigInteger(), nullable=False),
        sa.Column("usage_per_unit", sa.Numeric(18, 4), nullable=False, server_default="1"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sku_id", "consumable_id", name="uq_consumable_sku_mapping"),
    )
    op.create_index("ix_consumable_sku_mappings_sku_id", "consumable_sku_mappings", ["sku_id"])
    op.create_index("ix_consumable_sku_mappings_consumable_id", "consumable_sku_mappings", ["consumable_id"])

    op.create_table(
        "consumable_transactions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("consumable_id", sa.BigInteger(), nullable=False),
        sa.Column("transaction_type", sa.String(length=16), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(18, 4), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("source_id", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_type", "source_id", "consumable_id", name="uq_consumable_tx_source"),
    )
    op.create_index("ix_consumable_transactions_consumable_id", "consumable_transactions", ["consumable_id"])
    op.create_index("ix_consumable_transactions_transaction_type", "consumable_transactions", ["transaction_type"])


def downgrade() -> None:
    op.drop_index("ix_consumable_transactions_transaction_type", table_name="consumable_transactions")
    op.drop_index("ix_consumable_transactions_consumable_id", table_name="consumable_transactions")
    op.drop_table("consumable_transactions")
    op.drop_index("ix_consumable_sku_mappings_consumable_id", table_name="consumable_sku_mappings")
    op.drop_index("ix_consumable_sku_mappings_sku_id", table_name="consumable_sku_mappings")
    op.drop_table("consumable_sku_mappings")
    op.drop_index("ix_consumables_status", table_name="consumables")
    op.drop_index("ix_consumables_category", table_name="consumables")
    op.drop_index("ix_consumables_name", table_name="consumables")
    op.drop_table("consumables")
    op.drop_column("product_skus", "cost_tolerance_pct")
    op.drop_column("product_skus", "cost_mode")
