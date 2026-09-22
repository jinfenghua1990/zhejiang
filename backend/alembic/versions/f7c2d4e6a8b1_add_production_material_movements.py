"""add production material movements

Revision ID: f7c2d4e6a8b1
Revises: e6b1c3d5f7a9
"""

from alembic import op
import sqlalchemy as sa


revision = "f7c2d4e6a8b1"
down_revision = "e6b1c3d5f7a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "production_material_movements",
        sa.Column("movement_no", sa.String(48), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False),
        sa.Column("production_order_id", sa.BigInteger(), sa.ForeignKey("production_orders.id"), nullable=False),
        sa.Column("reservation_id", sa.BigInteger(), sa.ForeignKey("production_material_reservations.id"), nullable=False),
        sa.Column("consumable_id", sa.BigInteger(), sa.ForeignKey("consumables.id"), nullable=False),
        sa.Column("movement_type", sa.String(24), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("carrier", sa.String(128), nullable=False, server_default=""),
        sa.Column("tracking_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor", sa.String(128), nullable=False, server_default=""),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "request_key", "reservation_id", "movement_type",
            name="uq_production_material_movement_request",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_production_material_movement_qty"),
        sa.CheckConstraint(
            "movement_type IN ('dispatch', 'factory_receive')",
            name="ck_production_material_movement_type",
        ),
    )
    op.create_index("ix_production_material_movements_movement_no", "production_material_movements", ["movement_no"])
    op.create_index("ix_production_material_movements_request_key", "production_material_movements", ["request_key"])
    op.create_index("ix_production_material_movements_production_order_id", "production_material_movements", ["production_order_id"])
    op.create_index("ix_production_material_movements_reservation_id", "production_material_movements", ["reservation_id"])
    op.create_index("ix_production_material_movements_consumable_id", "production_material_movements", ["consumable_id"])
    op.create_index("ix_production_material_movements_movement_type", "production_material_movements", ["movement_type"])
    op.create_index("ix_production_material_movements_tracking_no", "production_material_movements", ["tracking_no"])
    op.create_index("ix_production_material_movements_occurred_at", "production_material_movements", ["occurred_at"])
    op.create_index(
        "ix_production_material_movements_order_time",
        "production_material_movements",
        ["production_order_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_table("production_material_movements")
