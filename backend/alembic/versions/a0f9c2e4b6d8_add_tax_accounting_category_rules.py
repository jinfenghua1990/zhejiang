"""add editable tax accounting category rules

Revision ID: a0f9c2e4b6d8
Revises: z8c1e4f6b8d0
"""
from alembic import op
import sqlalchemy as sa

revision = "a0f9c2e4b6d8"
down_revision = "z8c1e4f6b8d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tax_accounting_category_rules",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("category_name", sa.String(length=128), nullable=False),
        sa.Column("item_name", sa.String(length=256), nullable=False),
        sa.Column("match_keyword", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("match_mode", sa.String(length=16), nullable=False, server_default="contains"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(length=64), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(length=64), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("category_name", "item_name", name="uq_tax_accounting_category_rule"),
    )
    op.create_index(
        "ix_tax_accounting_category_rules_enabled_priority",
        "tax_accounting_category_rules",
        ["enabled", "priority"],
        unique=False,
    )
    # 用户当前确认的首条规则：*软饮料*咖啡。后续可在页面/API自行新增、修改、停用。
    op.execute(
        """
        INSERT INTO tax_accounting_category_rules
            (category_name, item_name, match_keyword, match_mode, priority, enabled, note, created_by, updated_by)
        VALUES
            ('软饮料', '咖啡', '咖啡', 'contains', 100, true, '初始规则：*软饮料*咖啡', 'system', 'system')
        ON CONFLICT (category_name, item_name) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_tax_accounting_category_rules_enabled_priority", table_name="tax_accounting_category_rules")
    op.drop_table("tax_accounting_category_rules")
