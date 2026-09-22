"""add unified business partner archive

Revision ID: partner20260921
Revises: bankacct20260921
Create Date: 2026-09-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "partner20260921"
down_revision = "bankacct20260921"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "business_partners",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("legacy_supplier_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("normalized_name", sa.String(length=256), nullable=False),
        sa.Column("tax_no", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("contact", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("phone", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("address", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("bank_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("bank_account_no", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("bank_account_name", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("roles", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["legacy_supplier_id"], ["suppliers.id"],
            name="fk_business_partners_legacy_supplier", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_business_partners_legacy_supplier_id",
        "business_partners",
        ["legacy_supplier_id"],
        unique=True,
    )
    op.create_index("ix_business_partners_name", "business_partners", ["name"])
    op.create_index("ix_business_partners_normalized_name", "business_partners", ["normalized_name"])
    op.create_index("ix_business_partners_tax_no", "business_partners", ["tax_no"])
    op.create_index("ix_business_partners_bank_account_no", "business_partners", ["bank_account_no"])
    op.create_index("ix_business_partners_status", "business_partners", ["status"])

    op.create_table(
        "business_partner_identifiers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("partner_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("value", sa.String(length=512), nullable=False),
        sa.Column("normalized_value", sa.String(length=512), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["partner_id"], ["business_partners.id"],
            name="fk_business_partner_identifiers_partner", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "partner_id", "kind", "normalized_value",
            name="uq_business_partner_identifier_value",
        ),
    )
    op.create_index("ix_business_partner_identifiers_partner_id", "business_partner_identifiers", ["partner_id"])
    op.create_index("ix_business_partner_identifiers_kind", "business_partner_identifiers", ["kind"])
    op.create_index("ix_business_partner_identifiers_normalized_value", "business_partner_identifiers", ["normalized_value"])

    op.create_table(
        "business_partner_links",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("partner_id", sa.BigInteger(), nullable=True),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("relation_role", sa.String(length=32), nullable=False),
        sa.Column("raw_name", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("raw_tax_no", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("raw_account_no", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="linked"),
        sa.Column("match_method", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("candidate_partner_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["partner_id"], ["business_partners.id"],
            name="fk_business_partner_links_partner", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_type", "source_id", "relation_role",
            name="uq_business_partner_link_source_role",
        ),
    )
    op.create_index("ix_business_partner_links_partner_id", "business_partner_links", ["partner_id"])
    op.create_index("ix_business_partner_links_source_type", "business_partner_links", ["source_type"])
    op.create_index("ix_business_partner_links_source_id", "business_partner_links", ["source_id"])
    op.create_index("ix_business_partner_links_relation_role", "business_partner_links", ["relation_role"])
    op.create_index("ix_business_partner_links_status", "business_partner_links", ["status"])
    op.create_index("ix_business_partner_links_confirmed", "business_partner_links", ["confirmed"])


def downgrade() -> None:
    op.drop_index("ix_business_partner_links_confirmed", table_name="business_partner_links")
    op.drop_index("ix_business_partner_links_status", table_name="business_partner_links")
    op.drop_index("ix_business_partner_links_relation_role", table_name="business_partner_links")
    op.drop_index("ix_business_partner_links_source_id", table_name="business_partner_links")
    op.drop_index("ix_business_partner_links_source_type", table_name="business_partner_links")
    op.drop_index("ix_business_partner_links_partner_id", table_name="business_partner_links")
    op.drop_table("business_partner_links")
    op.drop_index("ix_business_partner_identifiers_normalized_value", table_name="business_partner_identifiers")
    op.drop_index("ix_business_partner_identifiers_kind", table_name="business_partner_identifiers")
    op.drop_index("ix_business_partner_identifiers_partner_id", table_name="business_partner_identifiers")
    op.drop_table("business_partner_identifiers")
    op.drop_index("ix_business_partners_status", table_name="business_partners")
    op.drop_index("ix_business_partners_bank_account_no", table_name="business_partners")
    op.drop_index("ix_business_partners_tax_no", table_name="business_partners")
    op.drop_index("ix_business_partners_normalized_name", table_name="business_partners")
    op.drop_index("ix_business_partners_name", table_name="business_partners")
    op.drop_index("ix_business_partners_legacy_supplier_id", table_name="business_partners")
    op.drop_table("business_partners")
