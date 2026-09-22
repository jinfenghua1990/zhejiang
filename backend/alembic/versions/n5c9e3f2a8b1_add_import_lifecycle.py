"""add import lifecycle (draft / active / deleted) for data center imports

Revision ID: n5c9e3f2a8b1
Revises: m4a7c2e9b5f1
Create Date: 2026-09-03

让 1688 / 吉客云 / 税务三类导入支持「"上待确认 → 确认 → 软删除 → 恢复」三态生命周期。
存量数据全部回填为 ``active``，与改造前一致；业务查询通过 join 导入表 lifecycle 列
过滤掉 draft / deleted 行，避免错误数据出现在采购链路/工作台/采购详情中。
``lifecycle_changed_at`` 用于 Celery 回收站定时清理（默认 30 天）。
"""
from alembic import op
import sqlalchemy as sa


revision = "n5c9e3f2a8b1"
down_revision = "m4a7c2e9b5f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("alibaba1688_file_imports", "jackyun_file_imports", "tax_invoice_imports"):
        op.add_column(
            table,
            sa.Column("lifecycle", sa.String(length=16), nullable=False, server_default=sa.text("'active'")),
        )
        op.add_column(
            table,
            sa.Column("lifecycle_changed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(
            op.f(f"ix_{table}_lifecycle"),
            table,
            ["lifecycle"],
            unique=False,
        )


def downgrade() -> None:
    for table in ("alibaba1688_file_imports", "jackyun_file_imports", "tax_invoice_imports"):
        op.drop_index(op.f(f"ix_{table}_lifecycle"), table_name=table)
        op.drop_column(table, "lifecycle_changed_at")
        op.drop_column(table, "lifecycle")