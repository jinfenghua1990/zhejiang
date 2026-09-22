"""add foreign trade workbench tables

Revision ID: ftrd20260919
Revises: 464934dfa603
Create Date: 2026-09-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "ftrd20260919"
down_revision = "464934dfa603"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(18, 4)
RATE = sa.Numeric(18, 6)


def upgrade() -> None:
    op.create_table(
        "foreign_trade_channels",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("channel_type", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("brand", sa.String(128), nullable=False, server_default=""),
        sa.Column("currency", sa.String(8), nullable=False, server_default="EUR"),
        sa.Column("countries", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("connected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_foreign_trade_channels_code"),
    )
    op.create_table(
        "foreign_trade_sku_mappings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("channel_code", sa.String(64), nullable=False),
        sa.Column("external_sku", sa.String(128), nullable=False),
        sa.Column("internal_sku", sa.String(128), nullable=False, server_default=""),
        sa.Column("product_name", sa.String(512), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_code", "external_sku", name="uq_foreign_trade_sku_channel_external"),
    )
    op.create_index("ix_foreign_trade_sku_channel", "foreign_trade_sku_mappings", ["channel_code"])
    op.create_index("ix_foreign_trade_sku_internal", "foreign_trade_sku_mappings", ["internal_sku"])

    op.create_table(
        "foreign_trade_orders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("channel_code", sa.String(64), nullable=False),
        sa.Column("external_order_no", sa.String(128), nullable=False),
        sa.Column("business_mode", sa.String(16), nullable=False, server_default="b2c"),
        sa.Column("brand", sa.String(128), nullable=False, server_default=""),
        sa.Column("country", sa.String(64), nullable=False, server_default=""),
        sa.Column("currency", sa.String(8), nullable=False, server_default="EUR"),
        sa.Column("gross_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("discount_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("shipping_income", MONEY, nullable=False, server_default="0"),
        sa.Column("paid_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("refund_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("payment_fee", MONEY, nullable=False, server_default="0"),
        sa.Column("purchase_cost", MONEY, nullable=False, server_default="0"),
        sa.Column("logistics_cost", MONEY, nullable=False, server_default="0"),
        sa.Column("exchange_rate_to_cny", RATE, nullable=False, server_default="1"),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("procurement_status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("fulfillment_status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("payment_status", sa.String(24), nullable=False, server_default="unpaid"),
        sa.Column("customer_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("customer_email", sa.String(256), nullable=False, server_default=""),
        sa.Column("ship_to", sa.Text(), nullable=False, server_default=""),
        sa.Column("carrier", sa.String(128), nullable=False, server_default=""),
        sa.Column("tracking_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("items", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_code", "external_order_no", name="uq_foreign_trade_order_channel_external"),
    )
    op.create_index("ix_foreign_trade_orders_status", "foreign_trade_orders", ["status"])
    op.create_index("ix_foreign_trade_orders_business_mode", "foreign_trade_orders", ["business_mode"])
    op.create_index("ix_foreign_trade_orders_channel", "foreign_trade_orders", ["channel_code"])
    op.create_index("ix_foreign_trade_orders_fulfillment", "foreign_trade_orders", ["fulfillment_status"])
    op.create_index("ix_foreign_trade_orders_ordered_at", "foreign_trade_orders", ["ordered_at"])


def downgrade() -> None:
    op.drop_index("ix_foreign_trade_orders_ordered_at", table_name="foreign_trade_orders")
    op.drop_index("ix_foreign_trade_orders_business_mode", table_name="foreign_trade_orders")
    op.drop_index("ix_foreign_trade_orders_fulfillment", table_name="foreign_trade_orders")
    op.drop_index("ix_foreign_trade_orders_channel", table_name="foreign_trade_orders")
    op.drop_index("ix_foreign_trade_orders_status", table_name="foreign_trade_orders")
    op.drop_table("foreign_trade_orders")
    op.drop_index("ix_foreign_trade_sku_internal", table_name="foreign_trade_sku_mappings")
    op.drop_index("ix_foreign_trade_sku_channel", table_name="foreign_trade_sku_mappings")
    op.drop_table("foreign_trade_sku_mappings")
    op.drop_table("foreign_trade_channels")
