"""persist monthly business-source intake state

Revision ID: b7d9f1a3c5e7
Revises: a6c8e0f2b4d1
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b7d9f1a3c5e7"
down_revision = "a6c8e0f2b4d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monthly_intake_sources",
        sa.Column("company", sa.String(length=256), nullable=False),
        sa.Column("period_year", sa.BigInteger(), nullable=False),
        sa.Column("period_month", sa.BigInteger(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("archive_file_id", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="MISSING"),
        sa.Column("report_type", sa.String(length=32), nullable=False, server_default=""),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("error_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "company", "period_year", "period_month", "source_type",
            name="uq_monthly_intake_source",
        ),
    )
    op.create_index(
        "ix_monthly_intake_sources_company",
        "monthly_intake_sources",
        ["company"],
    )
    op.create_index(
        "ix_monthly_intake_sources_archive_file_id",
        "monthly_intake_sources",
        ["archive_file_id"],
    )
    op.create_index(
        "ix_monthly_intake_sources_status",
        "monthly_intake_sources",
        ["status"],
    )
    op.create_index(
        "ix_monthly_intake_sources_period",
        "monthly_intake_sources",
        ["company", "period_year", "period_month"],
    )


def downgrade() -> None:
    op.drop_index("ix_monthly_intake_sources_period", table_name="monthly_intake_sources")
    op.drop_index("ix_monthly_intake_sources_status", table_name="monthly_intake_sources")
    op.drop_index("ix_monthly_intake_sources_archive_file_id", table_name="monthly_intake_sources")
    op.drop_index("ix_monthly_intake_sources_company", table_name="monthly_intake_sources")
    op.drop_table("monthly_intake_sources")
