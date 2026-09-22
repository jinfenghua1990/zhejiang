"""Internal consumable purchasing and partial receipts.

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
"""

from alembic import op
import sqlalchemy as sa

revision = "c4d5e6f7a8b9"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade():
    op.create_table(
        "consumable_purchases",
        sa.Column("number", sa.String(40), nullable=False, unique=True),
        sa.Column("request_key", sa.String(36), nullable=False, unique=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("supplier_name", sa.String(256), nullable=False),
        sa.Column("ordered_on", sa.Date(), nullable=False),
        sa.Column("source_order_id", sa.BigInteger(), sa.ForeignKey("alibaba1688_orders.id"), nullable=True),
        sa.Column("reference_no", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_consumable_purchases_status", "consumable_purchases", ["status"])
    op.create_index("uq_consumable_purchase_source", "consumable_purchases", ["source_order_id"], unique=True, postgresql_where=sa.text("status <> 'cancelled'"))
    op.create_table(
        "consumable_purchase_items",
        sa.Column("purchase_id", sa.BigInteger(), sa.ForeignKey("consumable_purchases.id"), nullable=False),
        sa.Column("consumable_id", sa.BigInteger(), sa.ForeignKey("consumables.id"), nullable=False),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("received_qty", sa.Numeric(18, 4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(18, 4), nullable=False),
        sa.UniqueConstraint("purchase_id", "consumable_id", name="uq_consumable_purchase_item"),
        sa.CheckConstraint("quantity > 0 AND received_qty >= 0 AND received_qty <= quantity AND unit_cost >= 0", name="ck_consumable_purchase_quantities"),
        *timestamps(),
    )
    op.create_index("ix_consumable_purchase_items_purchase_id", "consumable_purchase_items", ["purchase_id"])
    op.create_table(
        "consumable_receipts",
        sa.Column("purchase_id", sa.BigInteger(), sa.ForeignKey("consumable_purchases.id"), nullable=False),
        sa.Column("number", sa.String(40), nullable=False, unique=True),
        sa.Column("request_key", sa.String(36), nullable=False, unique=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_consumable_receipts_purchase_id", "consumable_receipts", ["purchase_id"])
    op.add_column("consumable_transactions", sa.Column("request_key", sa.String(36), nullable=True))
    op.create_unique_constraint("uq_consumable_tx_request", "consumable_transactions", ["request_key"])


def downgrade():
    op.drop_constraint("uq_consumable_tx_request", "consumable_transactions", type_="unique")
    op.drop_column("consumable_transactions", "request_key")
    op.drop_table("consumable_receipts")
    op.drop_table("consumable_purchase_items")
    op.drop_table("consumable_purchases")
