"""add inventory stocktake tasks and items

Revision ID: stocktake20260921
Revises: partnerdup20260921
Create Date: 2026-09-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "stocktake20260921"
down_revision = "partnerdup20260921"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_stocktake_tasks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("number", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("item_kinds", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("search_text", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("category_filter", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(length=128), nullable=False, server_default="system"),
        sa.Column("confirmed_by", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["warehouse_id"], ["warehouses.id"],
            name="fk_inventory_stocktake_tasks_warehouse", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("number", name="uq_inventory_stocktake_tasks_number"),
    )
    op.create_index("ix_inventory_stocktake_tasks_number", "inventory_stocktake_tasks", ["number"])
    op.create_index("ix_inventory_stocktake_tasks_scope", "inventory_stocktake_tasks", ["scope"])
    op.create_index("ix_inventory_stocktake_tasks_status", "inventory_stocktake_tasks", ["status"])
    op.create_index("ix_inventory_stocktake_tasks_warehouse_id", "inventory_stocktake_tasks", ["warehouse_id"])

    op.create_table(
        "inventory_stocktake_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("item_kind", sa.String(length=16), nullable=False),
        sa.Column("ref_id", sa.BigInteger(), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("name", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("unit", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("book_qty", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("actual_qty", sa.Numeric(18, 4), nullable=True),
        sa.Column("difference_qty", sa.Numeric(18, 4), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"], ["inventory_stocktake_tasks.id"],
            name="fk_inventory_stocktake_items_task", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"], ["warehouses.id"],
            name="fk_inventory_stocktake_items_warehouse", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id", "item_kind", "ref_id", "warehouse_id",
            name="uq_inventory_stocktake_task_item",
        ),
    )
    op.create_index("ix_inventory_stocktake_items_task_id", "inventory_stocktake_items", ["task_id"])
    op.create_index("ix_inventory_stocktake_items_item_kind", "inventory_stocktake_items", ["item_kind"])
    op.create_index("ix_inventory_stocktake_items_ref_id", "inventory_stocktake_items", ["ref_id"])
    op.create_index("ix_inventory_stocktake_items_warehouse_id", "inventory_stocktake_items", ["warehouse_id"])


def downgrade() -> None:
    op.drop_index("ix_inventory_stocktake_items_warehouse_id", table_name="inventory_stocktake_items")
    op.drop_index("ix_inventory_stocktake_items_ref_id", table_name="inventory_stocktake_items")
    op.drop_index("ix_inventory_stocktake_items_item_kind", table_name="inventory_stocktake_items")
    op.drop_index("ix_inventory_stocktake_items_task_id", table_name="inventory_stocktake_items")
    op.drop_table("inventory_stocktake_items")

    op.drop_index("ix_inventory_stocktake_tasks_warehouse_id", table_name="inventory_stocktake_tasks")
    op.drop_index("ix_inventory_stocktake_tasks_status", table_name="inventory_stocktake_tasks")
    op.drop_index("ix_inventory_stocktake_tasks_scope", table_name="inventory_stocktake_tasks")
    op.drop_index("ix_inventory_stocktake_tasks_number", table_name="inventory_stocktake_tasks")
    op.drop_table("inventory_stocktake_tasks")
