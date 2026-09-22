"""expand invoice payment method for platform auto debit

Revision ID: pmtv20260922_auto_debit
Revises: alsvid20260922_products
Create Date: 2026-09-22
"""

from alembic import op
import sqlalchemy as sa


revision = "pmtv20260922_auto_debit"
down_revision = "alsvid20260922_products"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "tax_invoices",
        "payment_method",
        existing_type=sa.String(length=16),
        type_=sa.String(length=32),
        existing_nullable=False,
        existing_server_default="",
    )


def downgrade() -> None:
    op.alter_column(
        "tax_invoices",
        "payment_method",
        existing_type=sa.String(length=32),
        type_=sa.String(length=16),
        existing_nullable=False,
        existing_server_default="",
    )
