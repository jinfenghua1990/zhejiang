"""require consumable usage decision when confirming inbound links

Revision ID: w5f7a9c1e3b2
Revises: v4e6a8b0c2d3
"""

from alembic import op
import sqlalchemy as sa


revision = "w5f7a9c1e3b2"
down_revision = "v4e6a8b0c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "procurement_chain_links",
        sa.Column("consumable_usage_decided", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "procurement_chain_links",
        sa.Column("consumable_usage_enabled", sa.Boolean(), nullable=True),
    )
    op.create_index(
        "ix_procurement_chain_links_consumable_usage_decided",
        "procurement_chain_links",
        ["consumable_usage_decided"],
    )
    op.create_table(
        "inbound_consumable_usages",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("link_id", sa.BigInteger(), nullable=False),
        sa.Column("inbound_document_id", sa.BigInteger(), nullable=False),
        sa.Column("consumable_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint("link_id", "consumable_id", name="uq_inbound_consumable_usage"),
    )
    op.create_index("ix_inbound_consumable_usages_link_id", "inbound_consumable_usages", ["link_id"])
    op.create_index("ix_inbound_consumable_usages_inbound_document_id", "inbound_consumable_usages", ["inbound_document_id"])
    op.create_index("ix_inbound_consumable_usages_consumable_id", "inbound_consumable_usages", ["consumable_id"])


def downgrade() -> None:
    op.drop_index("ix_inbound_consumable_usages_consumable_id", table_name="inbound_consumable_usages")
    op.drop_index("ix_inbound_consumable_usages_inbound_document_id", table_name="inbound_consumable_usages")
    op.drop_index("ix_inbound_consumable_usages_link_id", table_name="inbound_consumable_usages")
    op.drop_table("inbound_consumable_usages")
    op.drop_index("ix_procurement_chain_links_consumable_usage_decided", table_name="procurement_chain_links")
    op.drop_column("procurement_chain_links", "consumable_usage_enabled")
    op.drop_column("procurement_chain_links", "consumable_usage_decided")
