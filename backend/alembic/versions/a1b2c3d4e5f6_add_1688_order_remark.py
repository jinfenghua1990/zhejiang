"""add 1688 order remark

Revision ID: a1b2c3d4e5f6
Revises: z8c1e4f6b8d0
"""

from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "z8c1e4f6b8d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alibaba1688_orders",
        sa.Column("order_remark", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("alibaba1688_orders", "order_remark")
