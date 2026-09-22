"""add foreign trade shipment tracking

Revision ID: ftrd20260920
Revises: ftrd20260919b
Create Date: 2026-09-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "ftrd20260920"
down_revision = "ftrd20260919b"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(18, 4)
RATE = sa.Numeric(18, 6)


def upgrade() -> None:
    op.create_table(
        "foreign_trade_shipments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("shipment_no", sa.String(64), nullable=False),
        sa.Column("order_nos", postgresql.JSONB(), nullable=False, server_default="[]"),

        sa.Column("brand", sa.String(128), nullable=False, server_default=""),
        sa.Column("origin_country", sa.String(64), nullable=False, server_default="CN"),
        sa.Column("destination_country", sa.String(64), nullable=False, server_default="AT"),
        sa.Column("destination_city", sa.String(128), nullable=False, server_default=""),
        sa.Column("transport_mode", sa.String(24), nullable=False, server_default="sea"),
        sa.Column("incoterm", sa.String(16), nullable=False, server_default="FOB"),
        sa.Column("status", sa.String(32), nullable=False, server_default="preparing"),

        sa.Column("carrier", sa.String(128), nullable=False, server_default=""),
        sa.Column("booking_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("bill_of_lading_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("container_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("tracking_no", sa.String(128), nullable=False, server_default=""),

        sa.Column("export_customs_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("import_customs_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("commercial_invoice_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("eori_no", sa.String(128), nullable=False, server_default=""),

        sa.Column("hs_code", sa.String(32), nullable=False, server_default=""),
        sa.Column("cn_code", sa.String(32), nullable=False, server_default=""),
        sa.Column("manufacturer_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("taric_additional_code", sa.String(32), nullable=False, server_default=""),
        sa.Column("tax_rate_source", sa.Text(), nullable=False, server_default=""),
        sa.Column("tax_rate_checked_at", sa.DateTime(timezone=True), nullable=True),

        sa.Column("currency", sa.String(8), nullable=False, server_default="EUR"),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("declared_value", MONEY, nullable=False, server_default="0"),
        sa.Column("freight_to_eu", MONEY, nullable=False, server_default="0"),
        sa.Column("insurance", MONEY, nullable=False, server_default="0"),

        sa.Column("customs_rate", RATE, nullable=False, server_default="0"),
        sa.Column("anti_dumping_rate", RATE, nullable=False, server_default="0"),
        sa.Column("countervailing_rate", RATE, nullable=False, server_default="0"),
        sa.Column("import_vat_rate", RATE, nullable=False, server_default="20"),
        sa.Column("import_vat_recoverable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("import_vat_additional_base", MONEY, nullable=False, server_default="0"),

        sa.Column("clearance_fee", MONEY, nullable=False, server_default="0"),
        sa.Column("port_fee", MONEY, nullable=False, server_default="0"),
        sa.Column("last_mile_fee", MONEY, nullable=False, server_default="0"),
        sa.Column("other_import_fee", MONEY, nullable=False, server_default="0"),

        sa.Column("export_purchase_cost_cny", MONEY, nullable=False, server_default="0"),
        sa.Column("domestic_export_cost_cny", MONEY, nullable=False, server_default="0"),
        sa.Column("export_refund_base_cny", MONEY, nullable=False, server_default="0"),
        sa.Column("export_refund_rate", RATE, nullable=False, server_default="0"),
        sa.Column("actual_export_refund_cny", MONEY, nullable=False, server_default="0"),
        sa.Column("export_refund_status", sa.String(24), nullable=False, server_default="pending"),

        sa.Column("eur_to_cny", RATE, nullable=False, server_default="1"),

        sa.Column("etd", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("departed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("arrived_eu_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("customs_cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),

        sa.Column("milestones", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("documents", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw", postgresql.JSONB(), nullable=False, server_default="{}"),

        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),

        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shipment_no", name="uq_foreign_trade_shipments_no"),
    )
    op.create_index("ix_foreign_trade_shipments_status", "foreign_trade_shipments", ["status"])
    op.create_index("ix_foreign_trade_shipments_tracking_no", "foreign_trade_shipments", ["tracking_no"])
    op.create_index("ix_foreign_trade_shipments_bl", "foreign_trade_shipments", ["bill_of_lading_no"])
    op.create_index("ix_foreign_trade_shipments_container", "foreign_trade_shipments", ["container_no"])
    op.create_index("ix_foreign_trade_shipments_eta", "foreign_trade_shipments", ["eta"])


def downgrade() -> None:
    op.drop_index("ix_foreign_trade_shipments_eta", table_name="foreign_trade_shipments")
    op.drop_index("ix_foreign_trade_shipments_container", table_name="foreign_trade_shipments")
    op.drop_index("ix_foreign_trade_shipments_bl", table_name="foreign_trade_shipments")
    op.drop_index("ix_foreign_trade_shipments_tracking_no", table_name="foreign_trade_shipments")
    op.drop_index("ix_foreign_trade_shipments_status", table_name="foreign_trade_shipments")
    op.drop_table("foreign_trade_shipments")
