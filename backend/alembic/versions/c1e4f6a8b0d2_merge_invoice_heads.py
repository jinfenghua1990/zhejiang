"""merge the invoice migration branches

Revision ID: c1e4f6a8b0d2
Revises: a9d3f6c2b5e1, b0d2e4f6a8c3
"""

from alembic import op


revision = "c1e4f6a8b0d2"
down_revision = ("a9d3f6c2b5e1", "b0d2e4f6a8c3")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
