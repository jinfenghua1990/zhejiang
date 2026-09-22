"""merge tax-accounting and supply-chain heads

Revision ID: ca37bfabdb8c
Revises: a0f9c2e4b6d8, i2f4a6c8e0b1
Create Date: 2026-09-08 11:14:25.442500
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'ca37bfabdb8c'
down_revision: Union[str, None] = ('a0f9c2e4b6d8', 'i2f4a6c8e0b1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
