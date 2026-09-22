"""align model indexes and uniqueness with the live schema

The application models moved from legacy index names to SQLAlchemy's current
column-derived names.  This migration keeps the same indexed columns and
uniqueness guarantees; it only removes obsolete names and recreates the
equivalent indexes so ``alembic check`` and fresh deployments agree.

Revision ID: d3e5f7a9c1b2
Revises: c2d4e6f8a0b1
"""

from alembic import op


revision = "d3e5f7a9c1b2"
down_revision = "c2d4e6f8a0b1"
branch_labels = None
depends_on = None


def _drop_index(name: str) -> None:
    op.execute(f"DROP INDEX IF EXISTS {name}")


def _create_index(name: str, table: str, column: str, *, unique: bool = False) -> None:
    qualifier = "UNIQUE " if unique else ""
    op.execute(
        f"CREATE {qualifier}INDEX IF NOT EXISTS {name} ON {table} ({column})"
    )


def upgrade() -> None:
    _create_index("ix_consumables_barcode", "consumables", "barcode")

    for old_name in (
        "ix_jky_web_sales_items_barcode",
        "ix_jky_web_sales_items_brand",
    ):
        _drop_index(old_name)
    _create_index("ix_jky_web_sales_order_items_barcode", "jky_web_sales_order_items", "barcode")
    _create_index("ix_jky_web_sales_order_items_brand_name", "jky_web_sales_order_items", "brand_name")

    op.execute(
        "ALTER TABLE jky_web_sales_orders "
        "DROP CONSTRAINT IF EXISTS uq_jky_web_sales_orders_trade_no"
    )
    _drop_index("ix_jky_web_sales_orders_trade_no")
    _create_index("ix_jky_web_sales_orders_trade_no", "jky_web_sales_orders", "trade_no", unique=True)

    _drop_index("ix_jky_web_stockin_orders_supplier")
    _create_index("ix_jky_web_stockin_orders_supplier_name", "jky_web_stockin_orders", "supplier_name")

    _drop_index("ix_jky_web_total_stock_brand")
    _create_index("ix_jky_web_total_stock_brand_name", "jky_web_total_stock", "brand_name")

    _drop_index("ix_jky_web_wh_stock_barcode")
    _create_index("ix_jky_web_warehouse_stock_barcode", "jky_web_warehouse_stock", "barcode")

    _drop_index("ix_tax_accounting_category_rules_enabled_priority")
    _create_index("ix_tax_accounting_category_rules_enabled", "tax_accounting_category_rules", "enabled")
    _create_index("ix_tax_accounting_category_rules_priority", "tax_accounting_category_rules", "priority")

    _drop_index("ix_warehouses_type")
    op.execute("ALTER TABLE warehouses DROP CONSTRAINT IF EXISTS uq_warehouses_code")
    _drop_index("ix_warehouses_code")
    _create_index("ix_warehouses_code", "warehouses", "code", unique=True)
    _create_index("ix_warehouses_warehouse_type", "warehouses", "warehouse_type")


def downgrade() -> None:
    _drop_index("ix_warehouses_warehouse_type")
    _drop_index("ix_warehouses_code")
    op.execute("ALTER TABLE warehouses ADD CONSTRAINT uq_warehouses_code UNIQUE (code)")
    _create_index("ix_warehouses_code", "warehouses", "code")
    _create_index("ix_warehouses_type", "warehouses", "warehouse_type")

    _drop_index("ix_tax_accounting_category_rules_enabled")
    _drop_index("ix_tax_accounting_category_rules_priority")
    _create_index(
        "ix_tax_accounting_category_rules_enabled_priority",
        "tax_accounting_category_rules",
        "enabled, priority",
    )

    _drop_index("ix_jky_web_warehouse_stock_barcode")
    _create_index("ix_jky_web_wh_stock_barcode", "jky_web_warehouse_stock", "barcode")
    _drop_index("ix_jky_web_total_stock_brand_name")
    _create_index("ix_jky_web_total_stock_brand", "jky_web_total_stock", "brand_name")
    _drop_index("ix_jky_web_stockin_orders_supplier_name")
    _create_index("ix_jky_web_stockin_orders_supplier", "jky_web_stockin_orders", "supplier_name")

    _drop_index("ix_jky_web_sales_orders_trade_no")
    op.execute(
        "ALTER TABLE jky_web_sales_orders "
        "ADD CONSTRAINT uq_jky_web_sales_orders_trade_no UNIQUE (trade_no)"
    )
    _create_index("ix_jky_web_sales_orders_trade_no", "jky_web_sales_orders", "trade_no")

    _drop_index("ix_jky_web_sales_order_items_brand_name")
    _drop_index("ix_jky_web_sales_order_items_barcode")
    _create_index("ix_jky_web_sales_items_brand", "jky_web_sales_order_items", "brand_name")
    _create_index("ix_jky_web_sales_items_barcode", "jky_web_sales_order_items", "barcode")

    _drop_index("ix_consumables_barcode")
