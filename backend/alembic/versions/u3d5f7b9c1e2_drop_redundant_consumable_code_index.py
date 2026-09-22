"""align consumable code index with unique constraint

Revision ID: u3d5f7b9c1e2
Revises: t2c4e6a8b0d1
"""

from alembic import op


revision = "u3d5f7b9c1e2"
down_revision = "t2c4e6a8b0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Older/local databases may contain this redundant non-unique index, while a
    # freshly migrated database may never have created it. Keep the migration
    # safe for both histories instead of failing when the index is absent.
    op.execute("DROP INDEX IF EXISTS ix_consumables_code")


def downgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_consumables_code ON consumables (code)")
