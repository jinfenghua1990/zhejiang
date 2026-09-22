"""tax_invoices.category：货款/报销/平台服务/未知 四类 (4)

Revision ID: b0d2e4f6a8c3
Revises: a9c2e4f6b8d1
"""

from alembic import op
import sqlalchemy as sa

revision = "b0d2e4f6a8c3"
down_revision = "a9c2e4f6b8d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tax_invoices",
        sa.Column("category", sa.String(32), nullable=False, server_default=""),
    )
    op.create_index("ix_tax_invoices_category", "tax_invoices", ["category"])


def downgrade() -> None:
    op.drop_index("ix_tax_invoices_category", table_name="tax_invoices")
    op.drop_column("tax_invoices", "category")
