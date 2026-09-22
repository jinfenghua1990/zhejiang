"""finance auto projection ownership and accounting effects

Revision ID: finp20260920
Revises: finc20260920
Create Date: 2026-09-20
"""
from alembic import op
import sqlalchemy as sa

revision = "finp20260920"
down_revision = "finc20260920"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "finance_entries",
        sa.Column("cash_effect", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "finance_entries",
        sa.Column("profit_effect", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.add_column(
        "foreign_trade_orders",
        sa.Column("seller_legal_entity_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_foreign_trade_orders_seller_legal_entity",
        "foreign_trade_orders",
        "finance_legal_entities",
        ["seller_legal_entity_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_foreign_trade_orders_seller_legal_entity_id",
        "foreign_trade_orders",
        ["seller_legal_entity_id"],
    )

    op.add_column(
        "foreign_trade_shipments",
        sa.Column("exporter_legal_entity_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "foreign_trade_shipments",
        sa.Column("importer_kind", sa.String(24), nullable=False, server_default="external_customer"),
    )
    op.add_column(
        "foreign_trade_shipments",
        sa.Column("importer_legal_entity_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "foreign_trade_shipments",
        sa.Column("export_refund_received_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_foreign_trade_shipments_exporter_legal_entity",
        "foreign_trade_shipments",
        "finance_legal_entities",
        ["exporter_legal_entity_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_foreign_trade_shipments_importer_legal_entity",
        "foreign_trade_shipments",
        "finance_legal_entities",
        ["importer_legal_entity_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_foreign_trade_shipments_exporter_legal_entity_id",
        "foreign_trade_shipments",
        ["exporter_legal_entity_id"],
    )
    op.create_index(
        "ix_foreign_trade_shipments_importer_kind",
        "foreign_trade_shipments",
        ["importer_kind"],
    )
    op.create_index(
        "ix_foreign_trade_shipments_importer_legal_entity_id",
        "foreign_trade_shipments",
        ["importer_legal_entity_id"],
    )

    # Existing foreign records belong to the current default China entity unless explicitly reassigned later.
    op.execute(
        """
        UPDATE foreign_trade_orders o
        SET seller_legal_entity_id = e.id
        FROM finance_legal_entities e
        WHERE o.seller_legal_entity_id IS NULL
          AND e.code = 'ZJCB'
        """
    )
    op.execute(
        """
        UPDATE foreign_trade_shipments s
        SET exporter_legal_entity_id = e.id
        FROM finance_legal_entities e
        WHERE s.exporter_legal_entity_id IS NULL
          AND e.code = 'ZJCB'
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_foreign_trade_shipments_importer_legal_entity_id",
        table_name="foreign_trade_shipments",
    )
    op.drop_index(
        "ix_foreign_trade_shipments_importer_kind",
        table_name="foreign_trade_shipments",
    )
    op.drop_index(
        "ix_foreign_trade_shipments_exporter_legal_entity_id",
        table_name="foreign_trade_shipments",
    )
    op.drop_constraint(
        "fk_foreign_trade_shipments_importer_legal_entity",
        "foreign_trade_shipments",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_foreign_trade_shipments_exporter_legal_entity",
        "foreign_trade_shipments",
        type_="foreignkey",
    )
    op.drop_column("foreign_trade_shipments", "export_refund_received_at")
    op.drop_column("foreign_trade_shipments", "importer_legal_entity_id")
    op.drop_column("foreign_trade_shipments", "importer_kind")
    op.drop_column("foreign_trade_shipments", "exporter_legal_entity_id")

    op.drop_index(
        "ix_foreign_trade_orders_seller_legal_entity_id",
        table_name="foreign_trade_orders",
    )
    op.drop_constraint(
        "fk_foreign_trade_orders_seller_legal_entity",
        "foreign_trade_orders",
        type_="foreignkey",
    )
    op.drop_column("foreign_trade_orders", "seller_legal_entity_id")

    op.drop_column("finance_entries", "profit_effect")
    op.drop_column("finance_entries", "cash_effect")
