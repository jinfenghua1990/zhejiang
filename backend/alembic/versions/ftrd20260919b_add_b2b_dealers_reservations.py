"""add B2B dealers and inventory reservations

Revision ID: ftrd20260919b
Revises: ftrd20260919
Create Date: 2026-09-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "ftrd20260919b"
down_revision = "ftrd20260919"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "foreign_trade_dealers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("company_name", sa.String(256), nullable=False),
        sa.Column("country", sa.String(64), nullable=False, server_default=""),
        sa.Column("region", sa.String(128), nullable=False, server_default=""),
        sa.Column("contact_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("email", sa.String(256), nullable=False, server_default=""),
        sa.Column("phone", sa.String(64), nullable=False, server_default=""),
        sa.Column("dealer_level", sa.String(32), nullable=False, server_default="standard"),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("currency", sa.String(8), nullable=False, server_default="EUR"),
        sa.Column("shopify_company_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("shopify_company_location_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("address", sa.Text(), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_foreign_trade_dealers_code"),
    )
    op.create_index("ix_foreign_trade_dealers_company", "foreign_trade_dealers", ["company_name"])
    op.create_index("ix_foreign_trade_dealers_shopify_location", "foreign_trade_dealers", ["shopify_company_location_id"])

    op.add_column("foreign_trade_orders", sa.Column("dealer_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_foreign_trade_orders_dealer_id",
        "foreign_trade_orders",
        "foreign_trade_dealers",
        ["dealer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_foreign_trade_orders_dealer_id", "foreign_trade_orders", ["dealer_id"])

    op.create_table(
        "foreign_trade_inventory_reservations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("dealer_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("reservation_kind", sa.String(24), nullable=False, server_default="quote"),
        sa.Column("reference_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("shopify_draft_order_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["dealer_id"], ["foreign_trade_dealers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sku_id"], ["product_skus.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ft_inventory_reservation_dealer", "foreign_trade_inventory_reservations", ["dealer_id"])
    op.create_index("ix_ft_inventory_reservation_sku", "foreign_trade_inventory_reservations", ["sku_id"])
    op.create_index("ix_ft_inventory_reservation_expires", "foreign_trade_inventory_reservations", ["expires_at"])
    op.create_index("ix_ft_inventory_reservation_status", "foreign_trade_inventory_reservations", ["status"])
    op.create_index("ix_ft_inventory_reservation_reference", "foreign_trade_inventory_reservations", ["reference_no"])


def downgrade() -> None:
    op.drop_index("ix_ft_inventory_reservation_reference", table_name="foreign_trade_inventory_reservations")
    op.drop_index("ix_ft_inventory_reservation_status", table_name="foreign_trade_inventory_reservations")
    op.drop_index("ix_ft_inventory_reservation_expires", table_name="foreign_trade_inventory_reservations")
    op.drop_index("ix_ft_inventory_reservation_sku", table_name="foreign_trade_inventory_reservations")
    op.drop_index("ix_ft_inventory_reservation_dealer", table_name="foreign_trade_inventory_reservations")
    op.drop_table("foreign_trade_inventory_reservations")
    op.drop_index("ix_foreign_trade_orders_dealer_id", table_name="foreign_trade_orders")
    op.drop_constraint("fk_foreign_trade_orders_dealer_id", "foreign_trade_orders", type_="foreignkey")
    op.drop_column("foreign_trade_orders", "dealer_id")
    op.drop_index("ix_foreign_trade_dealers_shopify_location", table_name="foreign_trade_dealers")
    op.drop_index("ix_foreign_trade_dealers_company", table_name="foreign_trade_dealers")
    op.drop_table("foreign_trade_dealers")
