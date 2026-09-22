"""add configurable monthly finance sales report templates

Revision ID: i2f4a6c8e0b1
Revises: h9e1c3f5a7b2
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "i2f4a6c8e0b1"
down_revision = "h9e1c3f5a7b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finance_sales_report_templates",
        sa.Column("company", sa.String(length=256), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False, server_default="默认财务月报"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("rules", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("to_addrs", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("cc_addrs", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("auto_send", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("send_day", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("send_hour", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "company", "name", name="uq_finance_sales_report_template_company_name"
        ),
        sa.CheckConstraint("send_day >= 1 AND send_day <= 28", name="ck_finance_sales_report_send_day"),
        sa.CheckConstraint("send_hour >= 0 AND send_hour <= 23", name="ck_finance_sales_report_send_hour"),
    )
    op.create_index(
        "ix_finance_sales_report_templates_company",
        "finance_sales_report_templates",
        ["company"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_finance_sales_report_templates_company",
        table_name="finance_sales_report_templates",
    )
    op.drop_table("finance_sales_report_templates")
