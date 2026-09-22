"""supplier profiles: tax_no / phone / address / notes / is_temp

Revision ID: c2d4e6f8a0b1
Revises: b7d9f1a3c5e7
Create Date: 2026-09-11
"""

from alembic import op
import sqlalchemy as sa

revision = "c2d4e6f8a0b1"
down_revision = "b7d9f1a3c5e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("suppliers", sa.Column("tax_no", sa.String(64), nullable=False, server_default=""))
    op.add_column("suppliers", sa.Column("phone", sa.String(64), nullable=False, server_default=""))
    op.add_column("suppliers", sa.Column("address", sa.String(512), nullable=False, server_default=""))
    op.add_column("suppliers", sa.Column("notes", sa.String(512), nullable=False, server_default=""))
    op.add_column("suppliers", sa.Column("is_temp", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.create_index(op.f("ix_suppliers_tax_no"), "suppliers", ["tax_no"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_suppliers_tax_no"), table_name="suppliers")
    op.drop_column("suppliers", "is_temp")
    op.drop_column("suppliers", "notes")
    op.drop_column("suppliers", "address")
    op.drop_column("suppliers", "phone")
    op.drop_column("suppliers", "tax_no")
