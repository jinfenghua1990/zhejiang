"""jky web adapter raw tables (6)

Revision ID: z8c1e4f6b8d0
Revises: y7b9d2f4a6c8e
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "z8c1e4f6b8d0"
down_revision = "y7b9d2f4a6c8e"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(18, 4)
QUANTITY = sa.Numeric(18, 4)


def upgrade() -> None:
    op.create_table(
        "jky_web_sales_orders",
        sa.Column("trade_no", sa.String(128), nullable=False),
        sa.Column("trade_status", sa.String(64), nullable=False, server_default=""),
        sa.Column("settle_status", sa.String(64), nullable=False, server_default=""),
        sa.Column("shop_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("shop_cate_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("source_trade_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("trade_type", sa.String(64), nullable=False, server_default=""),
        sa.Column("trade_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pay_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handle_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consign_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("warehouse_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("plat_warehouse_code", sa.String(64), nullable=False, server_default=""),
        sa.Column("logistic_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("logistic_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("trade_count", QUANTITY, nullable=True),
        sa.Column("goods_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("merge_remarks", sa.Text(), nullable=False, server_default=""),
        sa.Column("payment", MONEY, nullable=True),
        sa.Column("real_fee", MONEY, nullable=True),
        sa.Column("customer_code", sa.String(128), nullable=False, server_default=""),
        sa.Column("customer_account", sa.String(256), nullable=False, server_default=""),
        sa.Column("city", sa.String(64), nullable=False, server_default=""),
        sa.Column("raw", JSONB(), nullable=False, server_default="{}"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_no", name="uq_jky_web_sales_orders_trade_no"),
    )
    op.create_index("ix_jky_web_sales_orders_trade_no", "jky_web_sales_orders", ["trade_no"])
    op.create_index("ix_jky_web_sales_orders_trade_status", "jky_web_sales_orders", ["trade_status"])
    op.create_index("ix_jky_web_sales_orders_shop_name", "jky_web_sales_orders", ["shop_name"])
    op.create_index("ix_jky_web_sales_orders_source_trade_no", "jky_web_sales_orders", ["source_trade_no"])
    op.create_index("ix_jky_web_sales_orders_trade_time", "jky_web_sales_orders", ["trade_time"])
    op.create_index("ix_jky_web_sales_orders_logistic_no", "jky_web_sales_orders", ["logistic_no"])

    op.create_table(
        "jky_web_sales_order_items",
        sa.Column("trade_no", sa.String(128), nullable=False),
        sa.Column("source_trade_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("shop_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("shop_cate_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("goods_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("goods_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("barcode", sa.String(128), nullable=False, server_default=""),
        sa.Column("spec_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("brand_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("cate_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("warehouse_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("logistic_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("sell_count", QUANTITY, nullable=True),
        sa.Column("sell_price", MONEY, nullable=True),
        sa.Column("discount_fee", MONEY, nullable=True),
        sa.Column("discount_rate", MONEY, nullable=True),
        sa.Column("sell_total", MONEY, nullable=True),
        sa.Column("cost", MONEY, nullable=True),
        sa.Column("after_share_unit_fee", MONEY, nullable=True),
        sa.Column("share_favourable_fee", MONEY, nullable=True),
        sa.Column("after_share_fee", MONEY, nullable=True),
        sa.Column("other_share_fee", MONEY, nullable=True),
        sa.Column("gross_profit", MONEY, nullable=True),
        sa.Column("gross_profit_rate", MONEY, nullable=True),
        sa.Column("price1", MONEY, nullable=True),
        sa.Column("price6", MONEY, nullable=True),
        sa.Column("price7", MONEY, nullable=True),
        sa.Column("trade_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pay_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("customer_code", sa.String(128), nullable=False, server_default=""),
        sa.Column("raw", JSONB(), nullable=False, server_default="{}"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jky_web_sales_items_trade_no", "jky_web_sales_order_items", ["trade_no"])
    op.create_index("ix_jky_web_sales_items_goods_no", "jky_web_sales_order_items", ["goods_no"])
    op.create_index("ix_jky_web_sales_items_trade_time", "jky_web_sales_order_items", ["trade_time"])
    op.create_index("ix_jky_web_sales_items_shop", "jky_web_sales_order_items", ["shop_name"])
    op.create_index("ix_jky_web_sales_items_barcode", "jky_web_sales_order_items", ["barcode"])
    op.create_index("ix_jky_web_sales_items_brand", "jky_web_sales_order_items", ["brand_name"])

    op.create_table(
        "jky_web_total_stock",
        sa.Column("sku_key", sa.String(192), nullable=False),
        sa.Column("sku_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("goods_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("goods_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("goods_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("spec_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("barcode", sa.String(128), nullable=False, server_default=""),
        sa.Column("brand_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("cate_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("unit_name", sa.String(64), nullable=False, server_default=""),
        sa.Column("warehouse_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("warehouse_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("current_quantity", QUANTITY, nullable=True),
        sa.Column("locking_quantity", QUANTITY, nullable=True),
        sa.Column("can_use_quantity", QUANTITY, nullable=True),
        sa.Column("order_able_quantity", QUANTITY, nullable=True),
        sa.Column("yesterday_quantity", QUANTITY, nullable=True),
        sa.Column("week_quantity", QUANTITY, nullable=True),
        sa.Column("threeday_quantity", QUANTITY, nullable=True),
        sa.Column("total_sale_quantity", QUANTITY, nullable=True),
        sa.Column("price1", MONEY, nullable=True),
        sa.Column("price6", MONEY, nullable=True),
        sa.Column("price7", MONEY, nullable=True),
        sa.Column("cost_price", MONEY, nullable=True),
        sa.Column("cost_value", MONEY, nullable=True),
        sa.Column("raw", JSONB(), nullable=False, server_default="{}"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sku_key", name="uq_jky_web_total_stock_sku_key"),
    )
    op.create_index("ix_jky_web_total_stock_goods_no", "jky_web_total_stock", ["goods_no"])
    op.create_index("ix_jky_web_total_stock_barcode", "jky_web_total_stock", ["barcode"])
    op.create_index("ix_jky_web_total_stock_brand", "jky_web_total_stock", ["brand_name"])

    op.create_table(
        "jky_web_warehouse_stock",
        sa.Column("warehouse_key", sa.String(192), nullable=False),
        sa.Column("warehouse_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("warehouse_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("sku_key", sa.String(192), nullable=False),
        sa.Column("sku_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("goods_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("goods_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("goods_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("spec_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("barcode", sa.String(128), nullable=False, server_default=""),
        sa.Column("brand_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("cate_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("current_quantity", QUANTITY, nullable=True),
        sa.Column("can_use_quantity", QUANTITY, nullable=True),
        sa.Column("locking_quantity", QUANTITY, nullable=True),
        sa.Column("cost_price", MONEY, nullable=True),
        sa.Column("cost_value", MONEY, nullable=True),
        sa.Column("in_quantity_sum", QUANTITY, nullable=True),
        sa.Column("out_quantity_sum", QUANTITY, nullable=True),
        sa.Column("yesterday_quantity", QUANTITY, nullable=True),
        sa.Column("week_quantity", QUANTITY, nullable=True),
        sa.Column("threeday_quantity", QUANTITY, nullable=True),
        sa.Column("purchasing_quantity", QUANTITY, nullable=True),
        sa.Column("allocate_quantity", QUANTITY, nullable=True),
        sa.Column("sales_return_quantity", QUANTITY, nullable=True),
        sa.Column("last_stock_in_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw", JSONB(), nullable=False, server_default="{}"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("warehouse_key", "sku_key", name="uq_jky_web_wh_stock"),
    )
    op.create_index("ix_jky_web_wh_stock_goods_no", "jky_web_warehouse_stock", ["goods_no"])
    op.create_index("ix_jky_web_wh_stock_warehouse", "jky_web_warehouse_stock", ["warehouse_name"])
    op.create_index("ix_jky_web_wh_stock_barcode", "jky_web_warehouse_stock", ["barcode"])

    op.create_table(
        "jky_web_stockin_orders",
        sa.Column("doc_id", sa.String(64), nullable=False),
        sa.Column("goodsdoc_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("out_bill_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("in_out_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("inout_type", sa.String(16), nullable=False, server_default=""),
        sa.Column("inout_type_name", sa.String(64), nullable=False, server_default=""),
        sa.Column("warehouse_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("warehouse_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("bill_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("source_bill_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("supplier_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("logistic_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("logistic_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("company_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("total_quantity", QUANTITY, nullable=True),
        sa.Column("cost_total_amount", MONEY, nullable=True),
        sa.Column("has_tax_total_amount", MONEY, nullable=True),
        sa.Column("tax_total_amount", MONEY, nullable=True),
        sa.Column("red_status", sa.String(16), nullable=False, server_default=""),
        sa.Column("remark", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw", JSONB(), nullable=False, server_default="{}"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("doc_id", name="uq_jky_web_stockin_orders_doc_id"),
    )
    op.create_index("ix_jky_web_stockin_orders_goodsdoc_no", "jky_web_stockin_orders", ["goodsdoc_no"])
    op.create_index("ix_jky_web_stockin_orders_in_out_date", "jky_web_stockin_orders", ["in_out_date"])
    op.create_index("ix_jky_web_stockin_orders_warehouse_id", "jky_web_stockin_orders", ["warehouse_id"])
    op.create_index("ix_jky_web_stockin_orders_warehouse_name", "jky_web_stockin_orders", ["warehouse_name"])
    op.create_index("ix_jky_web_stockin_orders_bill_no", "jky_web_stockin_orders", ["bill_no"])
    op.create_index("ix_jky_web_stockin_orders_source_bill_no", "jky_web_stockin_orders", ["source_bill_no"])
    op.create_index("ix_jky_web_stockin_orders_supplier", "jky_web_stockin_orders", ["supplier_name"])

    op.create_table(
        "jky_web_stockin_items",
        sa.Column("rec_id", sa.String(64), nullable=False),
        sa.Column("doc_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("goodsdoc_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("order_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("goods_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("goods_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("goods_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("sku_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("spec_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("barcode", sa.String(128), nullable=False, server_default=""),
        sa.Column("brand_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("cate_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("quantity", QUANTITY, nullable=True),
        sa.Column("unit_name", sa.String(64), nullable=False, server_default=""),
        sa.Column("cost_price", MONEY, nullable=True),
        sa.Column("cost_amount", MONEY, nullable=True),
        sa.Column("with_tax_price", MONEY, nullable=True),
        sa.Column("with_tax_amount", MONEY, nullable=True),
        sa.Column("tax_rate", MONEY, nullable=True),
        sa.Column("batch_no", sa.String(128), nullable=False, server_default=""),
        sa.Column("production_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expiration_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shelf_life", sa.String(64), nullable=False, server_default=""),
        sa.Column("warehouse_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("raw", JSONB(), nullable=False, server_default="{}"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rec_id", name="uq_jky_web_stockin_items_rec_id"),
    )
    op.create_index("ix_jky_web_stockin_items_doc_id", "jky_web_stockin_items", ["doc_id"])
    op.create_index("ix_jky_web_stockin_items_goods_no", "jky_web_stockin_items", ["goods_no"])
    op.create_index("ix_jky_web_stockin_items_order_no", "jky_web_stockin_items", ["order_no"])
    op.create_index("ix_jky_web_stockin_items_sku_id", "jky_web_stockin_items", ["sku_id"])


def downgrade() -> None:
    op.drop_table("jky_web_stockin_items")
    op.drop_table("jky_web_stockin_orders")
    op.drop_table("jky_web_warehouse_stock")
    op.drop_table("jky_web_total_stock")
    op.drop_table("jky_web_sales_order_items")
    op.drop_table("jky_web_sales_orders")
