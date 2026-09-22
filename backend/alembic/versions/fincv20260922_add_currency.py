"""add currency to finance vouchers

Revision ID: fincv20260922_currency
Revises: fincv20260922
Create Date: 2026-09-22
"""
from alembic import op
import sqlalchemy as sa


revision = "fincv20260922_currency"
down_revision = "fincv20260922"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "finance_vouchers",
        sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"),
    )


def downgrade() -> None:
    op.drop_column("finance_vouchers", "currency")
