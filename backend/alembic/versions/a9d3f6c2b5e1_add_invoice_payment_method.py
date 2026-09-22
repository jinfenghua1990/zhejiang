"""add_invoice_payment_method

Revision ID: a9d3f6c2b5e1
Revises: z8c1e4f6b8d0
Create Date: 2026-09-17 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "a9d3f6c2b5e1"
down_revision = "z8c1e4f6b8d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tax_invoices",
        sa.Column("payment_method", sa.String(length=16), server_default="", nullable=False),
    )
    op.create_index("ix_tax_invoices_payment_method", "tax_invoices", ["payment_method"])


def downgrade() -> None:
    op.drop_index("ix_tax_invoices_payment_method", table_name="tax_invoices")
    op.drop_column("tax_invoices", "payment_method")
