"""add product type and consumable progress fields

Revision ID: v4e6a8b0c2d3
Revises: u3d5f7b9c1e2
"""

from alembic import op
import sqlalchemy as sa


revision = "v4e6a8b0c2d3"
down_revision = "u3d5f7b9c1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("product_skus", sa.Column("product_type", sa.String(length=16), nullable=False, server_default="single"))
    op.create_index("ix_product_skus_product_type", "product_skus", ["product_type"])
    op.execute("UPDATE product_skus SET product_type = CASE WHEN upper(sku_code) LIKE 'ES%' THEN 'bundle' ELSE 'single' END")
    op.add_column("consumables", sa.Column("purchased_qty", sa.Numeric(18, 4), nullable=False, server_default="0"))
    op.add_column("consumables", sa.Column("used_qty", sa.Numeric(18, 4), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("consumables", "used_qty")
    op.drop_column("consumables", "purchased_qty")
    op.drop_index("ix_product_skus_product_type", table_name="product_skus")
    op.drop_column("product_skus", "product_type")
