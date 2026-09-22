"""link financial classification rules with catalog tax codes

Revision ID: e4f7a9c2d6b1
Revises: d4f6a8c1e3b5
"""

from alembic import op
import sqlalchemy as sa


revision = "e4f7a9c2d6b1"
down_revision = "d4f6a8c1e3b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tax_accounting_category_rules",
        sa.Column("tax_code", sa.String(length=32), nullable=False, server_default=""),
    )
    for table in ("product_skus", "consumables"):
        op.add_column(table, sa.Column("tax_category_rule_id", sa.BigInteger(), nullable=True))
        op.create_index(
            f"ix_{table}_tax_category_rule_id",
            table,
            ["tax_category_rule_id"],
            unique=False,
        )
        op.create_foreign_key(
            f"fk_{table}_tax_category_rule_id",
            table,
            "tax_accounting_category_rules",
            ["tax_category_rule_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    for table in ("consumables", "product_skus"):
        op.drop_constraint(f"fk_{table}_tax_category_rule_id", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_tax_category_rule_id", table_name=table)
        op.drop_column(table, "tax_category_rule_id")
    op.drop_column("tax_accounting_category_rules", "tax_code")
