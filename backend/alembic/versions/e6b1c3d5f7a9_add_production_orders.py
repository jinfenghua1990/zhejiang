"""add production orders and material reservations

Revision ID: e6b1c3d5f7a9
Revises: d5a9f3c7e1b4
"""

from alembic import op
import sqlalchemy as sa


revision = "e6b1c3d5f7a9"
down_revision = "d5a9f3c7e1b4"
branch_labels = None
depends_on = None


def _timestamps():
    return [
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "production_orders",
        sa.Column("order_no", sa.String(40), nullable=False, unique=True),
        sa.Column("factory_name", sa.String(256), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="planned"),
        sa.Column("planned_start_date", sa.Date(), nullable=True),
        sa.Column("expected_delivery_date", sa.Date(), nullable=True),
        sa.Column("source_type", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(128), nullable=False, server_default=""),
        *_timestamps(),
    )
    op.create_index("ix_production_orders_status", "production_orders", ["status"])
    op.create_index(
        "ix_production_orders_status_expected",
        "production_orders",
        ["status", "expected_delivery_date"],
    )

    op.create_table(
        "production_order_items",
        sa.Column("production_order_id", sa.BigInteger(), sa.ForeignKey("production_orders.id"), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), sa.ForeignKey("product_skus.id"), nullable=False),
        sa.Column("sku_code", sa.String(128), nullable=False),
        sa.Column("sku_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("unit", sa.String(32), nullable=False, server_default=""),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("completed_qty", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.UniqueConstraint("production_order_id", "sku_id", name="uq_production_order_item_sku"),
        sa.CheckConstraint(
            "quantity > 0 AND completed_qty >= 0 AND completed_qty <= quantity",
            name="ck_production_order_item_quantities",
        ),
        *_timestamps(),
    )
    op.create_index("ix_production_order_items_production_order_id", "production_order_items", ["production_order_id"])
    op.create_index("ix_production_order_items_sku_id", "production_order_items", ["sku_id"])

    op.create_table(
        "production_material_reservations",
        sa.Column("production_order_id", sa.BigInteger(), sa.ForeignKey("production_orders.id"), nullable=False),
        sa.Column("consumable_id", sa.BigInteger(), sa.ForeignKey("consumables.id"), nullable=False),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False, server_default="个"),
        sa.Column("required_qty", sa.Numeric(18, 4), nullable=False),
        sa.Column("reserved_qty", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("dispatched_qty", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("factory_received_qty", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("consumed_qty", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.UniqueConstraint(
            "production_order_id",
            "consumable_id",
            name="uq_production_material_order_consumable",
        ),
        sa.CheckConstraint(
            "required_qty > 0 AND reserved_qty >= 0 AND dispatched_qty >= 0 "
            "AND factory_received_qty >= 0 AND consumed_qty >= 0 "
            "AND reserved_qty + dispatched_qty <= required_qty "
            "AND factory_received_qty <= dispatched_qty "
            "AND consumed_qty <= factory_received_qty",
            name="ck_production_material_quantities",
        ),
        *_timestamps(),
    )
    op.create_index(
        "ix_production_material_reservations_production_order_id",
        "production_material_reservations",
        ["production_order_id"],
    )
    op.create_index(
        "ix_production_material_reservations_consumable_id",
        "production_material_reservations",
        ["consumable_id"],
    )


def downgrade() -> None:
    op.drop_table("production_material_reservations")
    op.drop_table("production_order_items")
    op.drop_table("production_orders")
