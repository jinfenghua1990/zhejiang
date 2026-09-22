"""add target warehouse to purchase orders

Revision ID: f7a9c1d3e5b7
Revises: e5f7a9c1d3b7
"""

from alembic import op
import sqlalchemy as sa


revision = "f7a9c1d3e5b7"
down_revision = "e5f7a9c1d3b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "external_purchase_orders",
        sa.Column("warehouse_id", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_external_purchase_orders_warehouse_id",
        "external_purchase_orders",
        ["warehouse_id"],
    )
    op.create_foreign_key(
        "fk_external_purchase_orders_warehouse_id",
        "external_purchase_orders",
        "warehouses",
        ["warehouse_id"],
        ["id"],
    )
    op.add_column(
        "alibaba1688_orders",
        sa.Column("warehouse_id", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_alibaba1688_orders_warehouse_id",
        "alibaba1688_orders",
        ["warehouse_id"],
    )
    op.create_foreign_key(
        "fk_alibaba1688_orders_warehouse_id",
        "alibaba1688_orders",
        "warehouses",
        ["warehouse_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_alibaba1688_orders_warehouse_id", "alibaba1688_orders", type_="foreignkey")
    op.drop_index("ix_alibaba1688_orders_warehouse_id", table_name="alibaba1688_orders")
    op.drop_column("alibaba1688_orders", "warehouse_id")
    op.drop_constraint("fk_external_purchase_orders_warehouse_id", "external_purchase_orders", type_="foreignkey")
    op.drop_index("ix_external_purchase_orders_warehouse_id", table_name="external_purchase_orders")
    op.drop_column("external_purchase_orders", "warehouse_id")
