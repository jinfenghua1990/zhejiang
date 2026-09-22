"""align inbound consumable usage timestamps with TimestampMixin

Revision ID: x6a8c1e3f5b7d
Revises: w5f7a9c1e3b2
"""

from alembic import op
import sqlalchemy as sa


revision = "x6a8c1e3f5b7d"
down_revision = "w5f7a9c1e3b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("inbound_consumable_usages", "created_at", existing_type=sa.DateTime(timezone=True),
                    server_default=sa.text("now()"), nullable=False)
    op.alter_column("inbound_consumable_usages", "updated_at", existing_type=sa.DateTime(timezone=True),
                    server_default=sa.text("now()"), nullable=False)


def downgrade() -> None:
    op.alter_column("inbound_consumable_usages", "updated_at", existing_type=sa.DateTime(timezone=True),
                    server_default=None, nullable=True)
    op.alter_column("inbound_consumable_usages", "created_at", existing_type=sa.DateTime(timezone=True),
                    server_default=None, nullable=True)
