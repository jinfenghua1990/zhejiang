"""make product SKU codes unique

Revision ID: e5f7a9c1d3b7
Revises: d3e5f7a9c1b2
"""

from alembic import op


revision = "e5f7a9c1d3b7"
down_revision = "d3e5f7a9c1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_sku_code", "product_skus", ["sku_code"])


def downgrade() -> None:
    op.drop_constraint("uq_sku_code", "product_skus", type_="unique")
