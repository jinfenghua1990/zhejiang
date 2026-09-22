"""add fk indexes

Revision ID: 6d03dbaca68a
Revises: c65913dc8d5b
Create Date: 2026-09-01 19:20:00.000000

给高频/语义外键列补 B-tree 索引（此前仅依赖唯一约束顺带建立的索引）：
- stores.channel_id：门店↔渠道关联
- settlement_records.status：对账循环 filter(status != 'settled')
- reconciliation_matches.target_id：回款匹配按 target 反查（settled_amount_of）
- bank_import_batches.archive_file_id：银行导入批次↔归档文件
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '6d03dbaca68a'
down_revision: Union[str, None] = 'c65913dc8d5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(op.f('ix_stores_channel_id'), 'stores', ['channel_id'], unique=False)
    op.create_index(op.f('ix_settlement_records_status'), 'settlement_records', ['status'], unique=False)
    op.create_index(op.f('ix_reconciliation_matches_target_id'), 'reconciliation_matches', ['target_id'], unique=False)
    op.create_index(op.f('ix_bank_import_batches_archive_file_id'), 'bank_import_batches', ['archive_file_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_bank_import_batches_archive_file_id'), table_name='bank_import_batches')
    op.drop_index(op.f('ix_reconciliation_matches_target_id'), table_name='reconciliation_matches')
    op.drop_index(op.f('ix_settlement_records_status'), table_name='settlement_records')
    op.drop_index(op.f('ix_stores_channel_id'), table_name='stores')
