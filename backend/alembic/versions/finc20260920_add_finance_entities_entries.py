"""add finance legal entities and entries

Revision ID: finc20260920
Revises: ftrd20260920
Create Date: 2026-09-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "finc20260920"
down_revision = "ftrd20260920"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(18, 4)


def upgrade() -> None:
    op.create_table(
        "finance_legal_entities",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("country_code", sa.String(8), nullable=False, server_default="CN"),
        sa.Column("base_currency", sa.String(8), nullable=False, server_default="CNY"),
        sa.Column("tax_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("business_scopes", postgresql.JSONB(), nullable=False, server_default='["domestic","foreign_trade"]'),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_finance_legal_entities_code"),
        sa.UniqueConstraint("name", name="uq_finance_legal_entities_name"),
    )
    op.create_index("ix_finance_legal_entities_status", "finance_legal_entities", ["status"])
    op.create_index("ix_finance_legal_entities_is_default", "finance_legal_entities", ["is_default"])

    op.create_table(
        "finance_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("legal_entity_id", sa.BigInteger(), nullable=False),
        sa.Column("business_scope", sa.String(24), nullable=False, server_default="domestic"),
        sa.Column("source_type", sa.String(48), nullable=False, server_default="manual"),
        sa.Column("source_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("source_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(24), nullable=False, server_default="expense"),
        sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"),
        sa.Column("amount", MONEY, nullable=False, server_default="0"),
        sa.Column("tax_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("value_type", sa.String(16), nullable=False, server_default="actual"),
        sa.Column("settlement_status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("invoice_status", sa.String(24), nullable=False, server_default="unknown"),
        sa.Column("accounting_year", sa.Integer(), nullable=False),
        sa.Column("accounting_month", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"], ["finance_legal_entities.id"],
            name="fk_finance_entries_legal_entity", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "legal_entity_id", "source_type", "source_id", "category", "value_type",
            name="uq_finance_entry_source_category_value",
        ),
    )
    op.create_index("ix_finance_entries_legal_entity_id", "finance_entries", ["legal_entity_id"])
    op.create_index("ix_finance_entries_business_scope", "finance_entries", ["business_scope"])
    op.create_index("ix_finance_entries_source_type", "finance_entries", ["source_type"])
    op.create_index("ix_finance_entries_source_id", "finance_entries", ["source_id"])
    op.create_index("ix_finance_entries_source_no", "finance_entries", ["source_no"])
    op.create_index("ix_finance_entries_category", "finance_entries", ["category"])
    op.create_index("ix_finance_entries_direction", "finance_entries", ["direction"])
    op.create_index("ix_finance_entries_value_type", "finance_entries", ["value_type"])
    op.create_index("ix_finance_entries_settlement_status", "finance_entries", ["settlement_status"])
    op.create_index("ix_finance_entries_invoice_status", "finance_entries", ["invoice_status"])
    op.create_index("ix_finance_entries_accounting_year", "finance_entries", ["accounting_year"])
    op.create_index("ix_finance_entries_accounting_month", "finance_entries", ["accounting_month"])
    op.create_index(
        "ix_finance_entries_period_scope",
        "finance_entries",
        ["legal_entity_id", "accounting_year", "accounting_month", "business_scope"],
    )

    op.execute(
        """
        INSERT INTO finance_legal_entities
            (code, name, country_code, base_currency, status, is_default, business_scopes, note)
        VALUES
            ('ZJCB', '浙江柴本网络科技有限公司', 'CN', 'CNY', 'active', true,
             '["domestic","foreign_trade"]'::jsonb, '系统初始化默认主体')
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_finance_entries_period_scope", table_name="finance_entries")
    op.drop_index("ix_finance_entries_accounting_month", table_name="finance_entries")
    op.drop_index("ix_finance_entries_accounting_year", table_name="finance_entries")
    op.drop_index("ix_finance_entries_invoice_status", table_name="finance_entries")
    op.drop_index("ix_finance_entries_settlement_status", table_name="finance_entries")
    op.drop_index("ix_finance_entries_value_type", table_name="finance_entries")
    op.drop_index("ix_finance_entries_direction", table_name="finance_entries")
    op.drop_index("ix_finance_entries_category", table_name="finance_entries")
    op.drop_index("ix_finance_entries_source_no", table_name="finance_entries")
    op.drop_index("ix_finance_entries_source_id", table_name="finance_entries")
    op.drop_index("ix_finance_entries_source_type", table_name="finance_entries")
    op.drop_index("ix_finance_entries_business_scope", table_name="finance_entries")
    op.drop_index("ix_finance_entries_legal_entity_id", table_name="finance_entries")
    op.drop_table("finance_entries")
    op.drop_index("ix_finance_legal_entities_is_default", table_name="finance_legal_entities")
    op.drop_index("ix_finance_legal_entities_status", table_name="finance_legal_entities")
    op.drop_table("finance_legal_entities")
