"""preserve bank source row and distinguish serial from voucher

Revision ID: bankraw20260921
Revises: corpadj20260920
"""

from alembic import op
import sqlalchemy as sa


revision = "bankraw20260921"
down_revision = "corpadj20260920"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bank_transactions",
        sa.Column("transaction_time", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "bank_transactions",
        sa.Column("serial_no", sa.String(length=128), nullable=False, server_default=""),
    )
    op.add_column(
        "bank_transactions",
        sa.Column("source_row_number", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_bank_transactions_transaction_time",
        "bank_transactions",
        ["transaction_time"],
    )
    op.create_index(
        "ix_bank_transactions_serial_no",
        "bank_transactions",
        ["serial_no"],
    )


def downgrade() -> None:
    op.drop_index("ix_bank_transactions_serial_no", table_name="bank_transactions")
    op.drop_index("ix_bank_transactions_transaction_time", table_name="bank_transactions")
    op.drop_column("bank_transactions", "source_row_number")
    op.drop_column("bank_transactions", "serial_no")
    op.drop_column("bank_transactions", "transaction_time")
