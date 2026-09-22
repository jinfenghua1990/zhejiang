"""add production finished goods flow

Revision ID: g8d3e5f7a9b1
Revises: f7c2d4e6a8b1
"""

from alembic import op
import sqlalchemy as sa


revision = "g8d3e5f7a9b1"
down_revision = "f7c2d4e6a8b1"
branch_labels = None
depends_on = None


QTY = sa.Numeric(18, 4)


def upgrade() -> None:
    op.drop_constraint(
        "ck_production_order_item_quantities",
        "production_order_items",
        type_="check",
    )
    for name in ("shipped_qty", "arrived_qty", "inbound_qty"):
        op.add_column(
            "production_order_items",
            sa.Column(name, QTY, nullable=False, server_default="0"),
        )
    op.create_check_constraint(
        "ck_production_order_item_quantities",
        "production_order_items",
        "quantity > 0 "
        "AND completed_qty >= 0 AND shipped_qty >= 0 AND arrived_qty >= 0 AND inbound_qty >= 0 "
        "AND inbound_qty <= arrived_qty AND arrived_qty <= shipped_qty "
        "AND shipped_qty <= completed_qty AND completed_qty <= quantity",
    )

    op.drop_constraint(
        "ck_production_material_movement_type",
        "production_material_movements",
        type_="check",
    )
    op.create_check_constraint(
        "ck_production_material_movement_type",
        "production_material_movements",
        "movement_type IN ('dispatch', 'factory_receive', 'consume')",
    )

    op.create_table(
        "production_finished_movements",
        sa.Column("movement_no", sa.String(48), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False),
        sa.Column("production_order_id", sa.BigInteger(), sa.ForeignKey("production_orders.id"), nullable=False),
        sa.Column("production_order_item_id", sa.BigInteger(), sa.ForeignKey("production_order_items.id"), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), sa.ForeignKey("product_skus.id"), nullable=False),
        sa.Column("movement_type", sa.String(24), nullable=False),
        sa.Column("quantity", QTY, nullable=False),
        sa.Column("carrier", sa.String(128), nullable=False, server_default=""),
        sa.Column("tracking_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor", sa.String(128), nullable=False, server_default=""),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "request_key", "production_order_item_id", "movement_type",
            name="uq_production_finished_movement_request",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_production_finished_movement_qty"),
        sa.CheckConstraint(
            "movement_type IN ('complete', 'ship', 'arrive')",
            name="ck_production_finished_movement_type",
        ),
    )
    op.create_index("ix_production_finished_movements_movement_no", "production_finished_movements", ["movement_no"])
    op.create_index("ix_production_finished_movements_request_key", "production_finished_movements", ["request_key"])
    op.create_index("ix_production_finished_movements_production_order_id", "production_finished_movements", ["production_order_id"])
    op.create_index("ix_production_finished_movements_production_order_item_id", "production_finished_movements", ["production_order_item_id"])
    op.create_index("ix_production_finished_movements_sku_id", "production_finished_movements", ["sku_id"])
    op.create_index("ix_production_finished_movements_movement_type", "production_finished_movements", ["movement_type"])
    op.create_index("ix_production_finished_movements_tracking_no", "production_finished_movements", ["tracking_no"])
    op.create_index("ix_production_finished_movements_occurred_at", "production_finished_movements", ["occurred_at"])
    op.create_index(
        "ix_production_finished_movements_order_time",
        "production_finished_movements",
        ["production_order_id", "occurred_at"],
    )

    op.create_table(
        "production_inbound_allocations",
        sa.Column("request_key", sa.String(64), nullable=False),
        sa.Column("production_order_id", sa.BigInteger(), sa.ForeignKey("production_orders.id"), nullable=False),
        sa.Column("production_order_item_id", sa.BigInteger(), sa.ForeignKey("production_order_items.id"), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), sa.ForeignKey("product_skus.id"), nullable=False),
        sa.Column("inbound_document_id", sa.BigInteger(), sa.ForeignKey("jackyun_goods_documents.id"), nullable=False),
        sa.Column("inbound_item_id", sa.BigInteger(), sa.ForeignKey("jackyun_goods_document_items.id"), nullable=False),
        sa.Column("quantity", QTY, nullable=False),
        sa.Column("actor", sa.String(128), nullable=False, server_default=""),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "request_key", "production_order_item_id", "inbound_item_id",
            name="uq_production_inbound_allocation_request",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_production_inbound_allocation_qty"),
    )
    op.create_index("ix_production_inbound_allocations_request_key", "production_inbound_allocations", ["request_key"])
    op.create_index("ix_production_inbound_allocations_production_order_id", "production_inbound_allocations", ["production_order_id"])
    op.create_index("ix_production_inbound_allocations_production_order_item_id", "production_inbound_allocations", ["production_order_item_id"])
    op.create_index("ix_production_inbound_allocations_sku_id", "production_inbound_allocations", ["sku_id"])
    op.create_index("ix_production_inbound_allocations_inbound_document_id", "production_inbound_allocations", ["inbound_document_id"])
    op.create_index("ix_production_inbound_allocations_inbound_item_id", "production_inbound_allocations", ["inbound_item_id"])
    op.create_index("ix_production_inbound_allocations_linked_at", "production_inbound_allocations", ["linked_at"])
    op.create_index(
        "ix_production_inbound_allocations_order_doc",
        "production_inbound_allocations",
        ["production_order_id", "inbound_document_id"],
    )


def downgrade() -> None:
    op.drop_table("production_inbound_allocations")
    op.drop_table("production_finished_movements")

    op.drop_constraint(
        "ck_production_material_movement_type",
        "production_material_movements",
        type_="check",
    )
    op.create_check_constraint(
        "ck_production_material_movement_type",
        "production_material_movements",
        "movement_type IN ('dispatch', 'factory_receive')",
    )

    op.drop_constraint(
        "ck_production_order_item_quantities",
        "production_order_items",
        type_="check",
    )
    for name in ("inbound_qty", "arrived_qty", "shipped_qty"):
        op.drop_column("production_order_items", name)
    op.create_check_constraint(
        "ck_production_order_item_quantities",
        "production_order_items",
        "quantity > 0 AND completed_qty >= 0 AND completed_qty <= quantity",
    )
