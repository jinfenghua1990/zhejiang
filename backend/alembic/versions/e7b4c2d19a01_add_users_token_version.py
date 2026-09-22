"""add users.token_version for token revocation

Revision ID: e7b4c2d19a01
Revises: a1f2c3d4e5f6
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa

revision = "e7b4c2d19a01"
down_revision = "a1f2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
