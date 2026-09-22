"""Add 1688 order import tables

Revision ID: ccb1acb478e6
Revises: h1f6a8c3d5e7
Create Date: 2026-09-02 17:12:51.182755
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'ccb1acb478e6'
down_revision: Union[str, None] = 'h1f6a8c3d5e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1688 导出文件原件和订单副本。不要另建一套“imports”表：客户端导入和
    # 1688 导入都使用 file_imports 命名，避免同一批原始数据分裂成两套台账。
    op.create_table('alibaba1688_file_imports',
    sa.Column('original_name', sa.Text(), nullable=False),
    sa.Column('stored_path', sa.Text(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('size', sa.BigInteger(), nullable=False),
    sa.Column('mime', sa.String(length=128), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('sheet_name', sa.String(length=256), nullable=False),
    sa.Column('headers', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=False),
    sa.Column('imported_order_count', sa.Integer(), nullable=False),
    sa.Column('error_summary', sa.Text(), nullable=False),
    sa.Column('uploader', sa.String(length=64), nullable=False),
    sa.Column('parsed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('sha256', name='uq_alibaba1688_file_import_sha256')
    )
    op.create_index('ix_alibaba1688_file_imports_sha256', 'alibaba1688_file_imports', ['sha256'], unique=False)
    op.create_index('ix_alibaba1688_file_imports_status', 'alibaba1688_file_imports', ['status'], unique=False)

    op.create_table('alibaba1688_orders',
    sa.Column('external_order_id', sa.String(length=64), nullable=False),
    sa.Column('buyer_company_name', sa.String(length=256), nullable=False),
    sa.Column('buyer_member_name', sa.String(length=128), nullable=False),
    sa.Column('seller_company_name', sa.String(length=256), nullable=False),
    sa.Column('seller_member_name', sa.String(length=128), nullable=False),
    sa.Column('goods_total', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('freight', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('discount', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('actual_payment', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('order_status', sa.String(length=64), nullable=False),
    sa.Column('order_time', sa.DateTime(timezone=True), nullable=True),
    sa.Column('pay_time', sa.DateTime(timezone=True), nullable=True),
    sa.Column('raw_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('import_id', sa.BigInteger(), nullable=False),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('external_order_id', name='uq_alibaba1688_order_external_id')
    )
    op.create_index('ix_alibaba1688_orders_import_id', 'alibaba1688_orders', ['import_id'], unique=False)
    op.create_index('ix_alibaba1688_orders_external_order_id', 'alibaba1688_orders', ['external_order_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_alibaba1688_orders_external_order_id', table_name='alibaba1688_orders')
    op.drop_index('ix_alibaba1688_orders_import_id', table_name='alibaba1688_orders')
    op.drop_table('alibaba1688_orders')
    op.drop_index('ix_alibaba1688_file_imports_status', table_name='alibaba1688_file_imports')
    op.drop_index('ix_alibaba1688_file_imports_sha256', table_name='alibaba1688_file_imports')
    op.drop_table('alibaba1688_file_imports')
