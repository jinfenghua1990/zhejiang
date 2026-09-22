"""add users.hashed_password for login

Revision ID: a1f2c3d4e5f6
Revises: 6d03dbaca68a
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa

revision = "a1f2c3d4e5f6"
down_revision = "6d03dbaca68a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("hashed_password", sa.String(length=256), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "hashed_password")
