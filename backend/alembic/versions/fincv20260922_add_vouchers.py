"""add finance vouchers (auto bookkeeping)

Revision ID: fincv20260922
Revises: partnerv220260922
Create Date: 2026-09-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "fincv20260922"
down_revision = "partnerv220260922"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(18, 4)


def upgrade() -> None:
    op.create_table(
        "finance_vouchers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("legal_entity_id", sa.BigInteger(), nullable=False),
        sa.Column("voucher_no", sa.String(64), nullable=False),
        sa.Column("accounting_year", sa.Integer(), nullable=False),
        sa.Column("accounting_month", sa.Integer(), nullable=False),
        sa.Column("voucher_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="auto"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("legal_entity_id", "voucher_no", name="uq_finance_voucher_no"),
    )
    op.create_index("ix_finance_vouchers_period", "finance_vouchers",
                    ["legal_entity_id", "accounting_year", "accounting_month"])

    op.create_table(
        "finance_voucher_lines",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("voucher_id", sa.BigInteger(), nullable=False),
        sa.Column("entry_id", sa.BigInteger(), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("account_code", sa.String(32), nullable=False, server_default=""),
        sa.Column("account_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("amount", MONEY, nullable=False, server_default="0"),
        sa.Column("tax_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["voucher_id"], ["finance_vouchers.id"],
                                name="fk_voucher_lines_voucher", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_finance_voucher_lines_voucher", "finance_voucher_lines", ["voucher_id"])
    op.create_index("ix_finance_voucher_lines_entry", "finance_voucher_lines", ["entry_id"])


def downgrade() -> None:
    op.drop_index("ix_finance_voucher_lines_entry", table_name="finance_voucher_lines")
    op.drop_index("ix_finance_voucher_lines_voucher", table_name="finance_voucher_lines")
    op.drop_table("finance_voucher_lines")
    op.drop_index("ix_finance_vouchers_period", table_name="finance_vouchers")
    op.drop_table("finance_vouchers")
