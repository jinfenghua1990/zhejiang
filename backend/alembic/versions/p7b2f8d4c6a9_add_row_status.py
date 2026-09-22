"""add row_status for import row-level curation

Revision ID: p7b2f8d4c6a9
Revises: n5c9e3f2a8b1
Create Date: 2026-09-03

导入明细支持行级核对：用户可在「按表格直出的明细」里删除不要的行（可恢复）。
三张行表（1688 订单 / 吉客云导入行 / 税务导入行）增加 ``row_status``
（active=保留，deleted=已删除待恢复）。存量数据回填为 ``active``；
业务查询通过 ``row_status == 'active'`` 过滤已删行，
税务发票台账额外按来源行状态隐藏对应发票。
"""
from alembic import op
import sqlalchemy as sa


revision = "p7b2f8d4c6a9"
down_revision = "n5c9e3f2a8b1"
branch_labels = None
depends_on = None


TABLES = ("alibaba1688_orders", "jackyun_file_import_records", "tax_invoice_import_records")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(
            table,
            sa.Column("row_status", sa.String(length=16), nullable=False, server_default=sa.text("'active'")),
        )
        op.create_index(op.f(f"ix_{table}_row_status"), table, ["row_status"], unique=False)


def downgrade() -> None:
    for table in TABLES:
        op.drop_index(op.f(f"ix_{table}_row_status"), table_name=table)
        op.drop_column(table, "row_status")
