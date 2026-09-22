"""persist all confirmed Jackyun MCP business dimensions

Revision ID: g9e2f5b7c1d3
Revises: b2f7a913d4c6
Create Date: 2026-09-02

The MCP transport already retained raw responses, but purchase settlement,
purchase return, storage documents, allocation and shop-order responses had
no normalized local copy.  These tables keep the confirmed dimensions while
leaving the complete source record in ``raw`` for future field mapping.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "g9e2f5b7c1d3"
down_revision = "b2f7a913d4c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jackyun_purchase_settlements",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("settlement_no", sa.String(length=128), nullable=False),
        sa.Column("settlement_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supplier_name", sa.String(length=256), nullable=False),
        sa.Column("company_name", sa.String(length=256), nullable=False),
        sa.Column("total_amount", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("settlement_amount", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("settlement_type", sa.String(length=64), nullable=False),
        sa.Column("purchase_fee", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("paid", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("settlement_no"),
    )
    op.create_index("ix_jackyun_purchase_settlements_settlement_date", "jackyun_purchase_settlements", ["settlement_date"])
    op.create_index("ix_jackyun_purchase_settlements_supplier_name", "jackyun_purchase_settlements", ["supplier_name"])
    op.create_index("ix_jackyun_purchase_settlements_status", "jackyun_purchase_settlements", ["status"])

    op.create_table(
        "jackyun_purchase_returns",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("return_no", sa.String(length=128), nullable=False),
        sa.Column("purchase_no", sa.String(length=128), nullable=False),
        sa.Column("supplier_name", sa.String(length=256), nullable=False),
        sa.Column("warehouse_code", sa.String(length=64), nullable=False),
        sa.Column("warehouse_name", sa.String(length=256), nullable=False),
        sa.Column("created_at_src", sa.DateTime(timezone=True), nullable=True),
        sa.Column("returned_at_src", sa.DateTime(timezone=True), nullable=True),
        sa.Column("return_amount", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("return_no"),
    )
    op.create_index("ix_jackyun_purchase_returns_purchase_no", "jackyun_purchase_returns", ["purchase_no"])
    op.create_index("ix_jackyun_purchase_returns_supplier_name", "jackyun_purchase_returns", ["supplier_name"])
    op.create_index("ix_jackyun_purchase_returns_warehouse_code", "jackyun_purchase_returns", ["warehouse_code"])
    op.create_index("ix_jackyun_purchase_returns_created_at_src", "jackyun_purchase_returns", ["created_at_src"])
    op.create_index("ix_jackyun_purchase_returns_status", "jackyun_purchase_returns", ["status"])

    op.create_table(
        "jackyun_goods_documents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("document_type", sa.String(length=16), nullable=False),
        sa.Column("goodsdoc_no", sa.String(length=128), nullable=False),
        sa.Column("document_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("warehouse_code", sa.String(length=64), nullable=False),
        sa.Column("warehouse_name", sa.String(length=256), nullable=False),
        sa.Column("company_name", sa.String(length=256), nullable=False),
        sa.Column("total_quantity", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_type", "goodsdoc_no", name="uq_jackyun_goods_document_type_no"),
    )
    op.create_index("ix_jackyun_goods_documents_document_type", "jackyun_goods_documents", ["document_type"])
    op.create_index("ix_jackyun_goods_documents_goodsdoc_no", "jackyun_goods_documents", ["goodsdoc_no"])
    op.create_index("ix_jackyun_goods_documents_document_at", "jackyun_goods_documents", ["document_at"])
    op.create_index("ix_jackyun_goods_documents_warehouse_code", "jackyun_goods_documents", ["warehouse_code"])

    op.create_table(
        "jackyun_goods_document_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("goods_no", sa.String(length=128), nullable=False),
        sa.Column("sku_barcode", sa.String(length=128), nullable=False),
        sa.Column("goods_name", sa.String(length=512), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("unit_name", sa.String(length=32), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "line_no", name="uq_jackyun_goods_document_item_line"),
    )
    op.create_index("ix_jackyun_goods_document_items_document_id", "jackyun_goods_document_items", ["document_id"])
    op.create_index("ix_jackyun_goods_document_items_goods_no", "jackyun_goods_document_items", ["goods_no"])
    op.create_index("ix_jackyun_goods_document_items_sku_barcode", "jackyun_goods_document_items", ["sku_barcode"])

    op.create_table(
        "jackyun_stock_allocations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("allocate_no", sa.String(length=128), nullable=False),
        sa.Column("created_at_src", sa.DateTime(timezone=True), nullable=True),
        sa.Column("out_warehouse_code", sa.String(length=64), nullable=False),
        sa.Column("out_warehouse_name", sa.String(length=256), nullable=False),
        sa.Column("in_warehouse_code", sa.String(length=64), nullable=False),
        sa.Column("in_warehouse_name", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("allocate_no"),
    )
    op.create_index("ix_jackyun_stock_allocations_created_at_src", "jackyun_stock_allocations", ["created_at_src"])
    op.create_index("ix_jackyun_stock_allocations_out_warehouse_code", "jackyun_stock_allocations", ["out_warehouse_code"])
    op.create_index("ix_jackyun_stock_allocations_in_warehouse_code", "jackyun_stock_allocations", ["in_warehouse_code"])
    op.create_index("ix_jackyun_stock_allocations_status", "jackyun_stock_allocations", ["status"])

    op.create_table(
        "jackyun_shop_orders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("shop_order_no", sa.String(length=128), nullable=False),
        sa.Column("source_trade_no", sa.String(length=128), nullable=False),
        sa.Column("shop_name", sa.String(length=256), nullable=False),
        sa.Column("logistic_no", sa.String(length=128), nullable=False),
        sa.Column("api_type", sa.String(length=64), nullable=False),
        sa.Column("created_at_src", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at_src", sa.DateTime(timezone=True), nullable=True),
        sa.Column("goods_count", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("payment", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shop_order_no"),
    )
    op.create_index("ix_jackyun_shop_orders_source_trade_no", "jackyun_shop_orders", ["source_trade_no"])
    op.create_index("ix_jackyun_shop_orders_shop_name", "jackyun_shop_orders", ["shop_name"])
    op.create_index("ix_jackyun_shop_orders_logistic_no", "jackyun_shop_orders", ["logistic_no"])
    op.create_index("ix_jackyun_shop_orders_created_at_src", "jackyun_shop_orders", ["created_at_src"])
    op.create_index("ix_jackyun_shop_orders_status", "jackyun_shop_orders", ["status"])


def downgrade() -> None:
    op.drop_index("ix_jackyun_shop_orders_status", table_name="jackyun_shop_orders")
    op.drop_index("ix_jackyun_shop_orders_created_at_src", table_name="jackyun_shop_orders")
    op.drop_index("ix_jackyun_shop_orders_logistic_no", table_name="jackyun_shop_orders")
    op.drop_index("ix_jackyun_shop_orders_shop_name", table_name="jackyun_shop_orders")
    op.drop_index("ix_jackyun_shop_orders_source_trade_no", table_name="jackyun_shop_orders")
    op.drop_table("jackyun_shop_orders")

    op.drop_index("ix_jackyun_stock_allocations_status", table_name="jackyun_stock_allocations")
    op.drop_index("ix_jackyun_stock_allocations_in_warehouse_code", table_name="jackyun_stock_allocations")
    op.drop_index("ix_jackyun_stock_allocations_out_warehouse_code", table_name="jackyun_stock_allocations")
    op.drop_index("ix_jackyun_stock_allocations_created_at_src", table_name="jackyun_stock_allocations")
    op.drop_table("jackyun_stock_allocations")

    op.drop_index("ix_jackyun_goods_document_items_sku_barcode", table_name="jackyun_goods_document_items")
    op.drop_index("ix_jackyun_goods_document_items_goods_no", table_name="jackyun_goods_document_items")
    op.drop_index("ix_jackyun_goods_document_items_document_id", table_name="jackyun_goods_document_items")
    op.drop_table("jackyun_goods_document_items")

    op.drop_index("ix_jackyun_goods_documents_warehouse_code", table_name="jackyun_goods_documents")
    op.drop_index("ix_jackyun_goods_documents_document_at", table_name="jackyun_goods_documents")
    op.drop_index("ix_jackyun_goods_documents_goodsdoc_no", table_name="jackyun_goods_documents")
    op.drop_index("ix_jackyun_goods_documents_document_type", table_name="jackyun_goods_documents")
    op.drop_table("jackyun_goods_documents")

    op.drop_index("ix_jackyun_purchase_returns_status", table_name="jackyun_purchase_returns")
    op.drop_index("ix_jackyun_purchase_returns_created_at_src", table_name="jackyun_purchase_returns")
    op.drop_index("ix_jackyun_purchase_returns_warehouse_code", table_name="jackyun_purchase_returns")
    op.drop_index("ix_jackyun_purchase_returns_supplier_name", table_name="jackyun_purchase_returns")
    op.drop_index("ix_jackyun_purchase_returns_purchase_no", table_name="jackyun_purchase_returns")
    op.drop_table("jackyun_purchase_returns")

    op.drop_index("ix_jackyun_purchase_settlements_status", table_name="jackyun_purchase_settlements")
    op.drop_index("ix_jackyun_purchase_settlements_supplier_name", table_name="jackyun_purchase_settlements")
    op.drop_index("ix_jackyun_purchase_settlements_settlement_date", table_name="jackyun_purchase_settlements")
    op.drop_table("jackyun_purchase_settlements")
