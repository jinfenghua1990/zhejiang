"""add business partner duplicate reviews

Revision ID: partnerdup20260921
Revises: partner20260921
Create Date: 2026-09-21
"""

from alembic import op
import sqlalchemy as sa


revision = "partnerdup20260921"
down_revision = "partner20260921"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "business_partner_duplicate_reviews",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("partner_id", sa.BigInteger(), nullable=False),
        sa.Column("other_partner_id", sa.BigInteger(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("decided_by", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["partner_id"], ["business_partners.id"],
            name="fk_business_partner_duplicate_reviews_partner", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["other_partner_id"], ["business_partners.id"],
            name="fk_business_partner_duplicate_reviews_other", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "partner_id", "other_partner_id",
            name="uq_business_partner_duplicate_pair",
        ),
    )
    op.create_index(
        "ix_business_partner_duplicate_reviews_partner_id",
        "business_partner_duplicate_reviews",
        ["partner_id"],
    )
    op.create_index(
        "ix_business_partner_duplicate_reviews_other_partner_id",
        "business_partner_duplicate_reviews",
        ["other_partner_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_business_partner_duplicate_reviews_other_partner_id",
        table_name="business_partner_duplicate_reviews",
    )
    op.drop_index(
        "ix_business_partner_duplicate_reviews_partner_id",
        table_name="business_partner_duplicate_reviews",
    )
    op.drop_table("business_partner_duplicate_reviews")