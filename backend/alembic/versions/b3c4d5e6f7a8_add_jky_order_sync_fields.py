"""add unified Jackyun order source and identity fields

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "b3c4d5e6f7a8"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sales_orders",
        sa.Column("source_provider", sa.String(length=32), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "sales_orders",
        sa.Column("source_order_id", sa.String(length=128), nullable=False, server_default=""),
    )
    op.add_column(
        "sales_orders",
        sa.Column("identity_keys", JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "sales_orders",
        sa.Column("source_history", JSONB(), nullable=False, server_default="[]"),
    )
    op.create_index("ix_sales_orders_source_provider", "sales_orders", ["source_provider"])
    op.create_index("ix_sales_orders_source_order_id", "sales_orders", ["source_order_id"])
    op.create_index(
        "ix_sales_orders_identity_keys_gin",
        "sales_orders",
        ["identity_keys"],
        postgresql_using="gin",
    )
    # 同一订单编排任务只允许一个运行实例；不同任务类型不受影响。
    op.create_index(
        "uq_jky_order_sync_running",
        "sync_jobs",
        ["provider", "job_type"],
        unique=True,
        postgresql_where=sa.text("provider = 'jky_order' AND job_type = 'orders' AND status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("uq_jky_order_sync_running", table_name="sync_jobs")
    op.drop_index("ix_sales_orders_identity_keys_gin", table_name="sales_orders")
    op.drop_index("ix_sales_orders_source_order_id", table_name="sales_orders")
    op.drop_index("ix_sales_orders_source_provider", table_name="sales_orders")
    op.drop_column("sales_orders", "source_history")
    op.drop_column("sales_orders", "identity_keys")
    op.drop_column("sales_orders", "source_order_id")
    op.drop_column("sales_orders", "source_provider")
