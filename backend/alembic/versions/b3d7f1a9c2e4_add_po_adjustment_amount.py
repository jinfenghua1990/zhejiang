"""add 1688 adjustment amount on external purchase orders

Revision ID: b3d7f1a9c2e4
Revises: c4d5e6f7a8b9
"""

from alembic import op
import sqlalchemy as sa


revision = "b3d7f1a9c2e4"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "external_purchase_orders",
        sa.Column("adjustment_amount", sa.Numeric(18, 4), nullable=True),
    )
    op.add_column(
        "external_purchase_orders",
        sa.Column("adjustment_note", sa.String(256), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("external_purchase_orders", "adjustment_note")
    op.drop_column("external_purchase_orders", "adjustment_amount")
