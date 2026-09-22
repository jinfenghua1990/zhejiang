"""add jackyun file imports

Revision ID: b2f7a913d4c6
Revises: f8d1c4a7b2e0
Create Date: 2026-09-02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b2f7a913d4c6"
down_revision = "f8d1c4a7b2e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jackyun_file_imports",
        sa.Column("original_name", sa.Text(), nullable=False),
        sa.Column("stored_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("mime", sa.String(length=128), nullable=False),
        sa.Column("report_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("sheet_name", sa.String(length=256), nullable=False),
        sa.Column("headers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("staged_row_count", sa.Integer(), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=False),
        sa.Column("uploader", sa.String(length=64), nullable=False),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sha256", name="uq_jackyun_file_import_sha256"),
    )
    op.create_index("ix_jackyun_file_imports_sha256", "jackyun_file_imports", ["sha256"])
    op.create_index("ix_jackyun_file_imports_report_type", "jackyun_file_imports", ["report_type"])
    op.create_index("ix_jackyun_file_imports_status", "jackyun_file_imports", ["status"])

    op.create_table(
        "jackyun_file_import_records",
        sa.Column("import_id", sa.BigInteger(), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("import_id", "row_index", name="uq_jackyun_file_import_record"),
    )
    op.create_index("ix_jackyun_file_import_records_import_id", "jackyun_file_import_records", ["import_id"])


def downgrade() -> None:
    op.drop_index("ix_jackyun_file_import_records_import_id", table_name="jackyun_file_import_records")
    op.drop_table("jackyun_file_import_records")
    op.drop_index("ix_jackyun_file_imports_status", table_name="jackyun_file_imports")
    op.drop_index("ix_jackyun_file_imports_report_type", table_name="jackyun_file_imports")
    op.drop_index("ix_jackyun_file_imports_sha256", table_name="jackyun_file_imports")
    op.drop_table("jackyun_file_imports")
