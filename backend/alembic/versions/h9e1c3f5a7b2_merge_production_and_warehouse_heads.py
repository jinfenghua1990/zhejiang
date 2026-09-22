"""merge production-finished and configurable-warehouse heads

Revision ID: h9e1c3f5a7b2
Revises: g8d3e5f7a9b1, b9d4f6a8c2e1
"""

revision = "h9e1c3f5a7b2"
down_revision = ("g8d3e5f7a9b1", "b9d4f6a8c2e1")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Pure Alembic graph merge; both parent migrations already own their DDL."""
    pass


def downgrade() -> None:
    """Split the graph back to the two parent heads without changing data."""
    pass
