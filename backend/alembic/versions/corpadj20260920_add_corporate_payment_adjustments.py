"""persist monthly corporate-payment invoice selections

Revision ID: corpadj20260920
Revises: drift20260920
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "corpadj20260920"
down_revision = "drift20260920"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finance_corporate_payment_adjustments",
        sa.Column("company", sa.String(length=256), nullable=False),
        sa.Column("period_year", sa.BigInteger(), nullable=False),
        sa.Column("period_month", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column(
            "selected_keys",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("actor", sa.String(length=64), nullable=False, server_default="system"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "company", "period_year", "period_month", "version",
            name="uq_finance_corporate_payment_adjustment_version",
        ),
    )
    op.create_index(
        "ix_finance_corporate_payment_adjustments_company",
        "finance_corporate_payment_adjustments",
        ["company"],
    )
    op.create_index(
        "ix_finance_corporate_payment_adjustments_period",
        "finance_corporate_payment_adjustments",
        ["company", "period_year", "period_month"],
    )


def downgrade() -> None:
    op.drop_index("ix_finance_corporate_payment_adjustments_period", table_name="finance_corporate_payment_adjustments")
    op.drop_index("ix_finance_corporate_payment_adjustments_company", table_name="finance_corporate_payment_adjustments")
    op.drop_table("finance_corporate_payment_adjustments")
