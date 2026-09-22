"""add input invoice processing status

Revision ID: a9c2e4f6b8d1
Revises: f7a9c1d3e5b7
"""

from alembic import op
import sqlalchemy as sa


revision = "a9c2e4f6b8d1"
down_revision = "f7a9c1d3e5b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tax_invoices",
        sa.Column("processing_status", sa.String(length=16), nullable=False, server_default="pending"),
    )
    op.create_index(
        "ix_tax_invoices_processing_status",
        "tax_invoices",
        ["processing_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_tax_invoices_processing_status", table_name="tax_invoices")
    op.drop_column("tax_invoices", "processing_status")
