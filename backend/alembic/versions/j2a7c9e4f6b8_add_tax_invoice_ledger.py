"""add tax invoice import batches and normalized ledger

Revision ID: j2a7c9e4f6b8
Revises: h1f6a8c3d5e7
Create Date: 2026-09-02

Official tax-system exports are retained as immutable source files and rows,
while recognized invoice headers are normalized for status and business
matching.  Unrecognized rows remain reviewable instead of being discarded.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "j2a7c9e4f6b8"
down_revision = "h1f6a8c3d5e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tax_invoice_imports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("original_name", sa.Text(), nullable=False),
        sa.Column("stored_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("mime", sa.String(length=128), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("period_year", sa.Integer(), nullable=False),
        sa.Column("period_month", sa.Integer(), nullable=False),
        sa.Column("sheet_name", sa.String(length=256), nullable=False),
        sa.Column("headers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("mapping", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("recognized_row_count", sa.Integer(), nullable=False),
        sa.Column("matched_row_count", sa.Integer(), nullable=False),
        sa.Column("needs_review_count", sa.Integer(), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=False),
        sa.Column("uploader", sa.String(length=64), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sha256", name="uq_tax_invoice_import_sha256"),
    )
    op.create_index("ix_tax_invoice_imports_sha256", "tax_invoice_imports", ["sha256"])
    op.create_index("ix_tax_invoice_imports_source_system", "tax_invoice_imports", ["source_system"])
    op.create_index("ix_tax_invoice_imports_period_year", "tax_invoice_imports", ["period_year"])
    op.create_index("ix_tax_invoice_imports_period_month", "tax_invoice_imports", ["period_month"])
    op.create_index("ix_tax_invoice_imports_status", "tax_invoice_imports", ["status"])

    op.create_table(
        "tax_invoice_import_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("import_id", sa.BigInteger(), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("recognition_status", sa.String(length=32), nullable=False),
        sa.Column("invoice_id", sa.BigInteger(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("import_id", "row_index", name="uq_tax_invoice_import_record"),
    )
    op.create_index("ix_tax_invoice_import_records_import_id", "tax_invoice_import_records", ["import_id"])
    op.create_index("ix_tax_invoice_import_records_recognition_status", "tax_invoice_import_records", ["recognition_status"])
    op.create_index("ix_tax_invoice_import_records_invoice_id", "tax_invoice_import_records", ["invoice_id"])

    op.create_table(
        "tax_invoices",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("invoice_key", sa.String(length=256), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("invoice_code", sa.String(length=64), nullable=False),
        sa.Column("invoice_number", sa.String(length=128), nullable=False),
        sa.Column("invoice_type", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("issue_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("seller_name", sa.String(length=256), nullable=False),
        sa.Column("seller_tax_id", sa.String(length=64), nullable=False),
        sa.Column("buyer_name", sa.String(length=256), nullable=False),
        sa.Column("buyer_tax_id", sa.String(length=64), nullable=False),
        sa.Column("amount_excl_tax", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("tax_amount", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("total_amount", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_import_id", sa.BigInteger(), nullable=True),
        sa.Column("source_row_index", sa.Integer(), nullable=True),
        sa.Column("match_status", sa.String(length=32), nullable=False),
        sa.Column("match_note", sa.Text(), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invoice_key", name="uq_tax_invoice_key"),
    )
    op.create_index("ix_tax_invoices_direction", "tax_invoices", ["direction"])
    op.create_index("ix_tax_invoices_invoice_code", "tax_invoices", ["invoice_code"])
    op.create_index("ix_tax_invoices_invoice_number", "tax_invoices", ["invoice_number"])
    op.create_index("ix_tax_invoices_status", "tax_invoices", ["status"])
    op.create_index("ix_tax_invoices_issue_date", "tax_invoices", ["issue_date"])
    op.create_index("ix_tax_invoices_seller_name", "tax_invoices", ["seller_name"])
    op.create_index("ix_tax_invoices_seller_tax_id", "tax_invoices", ["seller_tax_id"])
    op.create_index("ix_tax_invoices_buyer_name", "tax_invoices", ["buyer_name"])
    op.create_index("ix_tax_invoices_buyer_tax_id", "tax_invoices", ["buyer_tax_id"])
    op.create_index("ix_tax_invoices_source_system", "tax_invoices", ["source_system"])
    op.create_index("ix_tax_invoices_source_import_id", "tax_invoices", ["source_import_id"])
    op.create_index("ix_tax_invoices_match_status", "tax_invoices", ["match_status"])

    op.create_table(
        "tax_invoice_links",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("invoice_id", sa.BigInteger(), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("allocated_amount", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("match_method", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invoice_id", "target_type", "target_id", name="uq_tax_invoice_link_target"),
    )
    op.create_index("ix_tax_invoice_links_invoice_id", "tax_invoice_links", ["invoice_id"])
    op.create_index("ix_tax_invoice_links_target_type", "tax_invoice_links", ["target_type"])
    op.create_index("ix_tax_invoice_links_target_id", "tax_invoice_links", ["target_id"])
    op.create_index("ix_tax_invoice_links_confirmed", "tax_invoice_links", ["confirmed"])


def downgrade() -> None:
    op.drop_index("ix_tax_invoice_links_confirmed", table_name="tax_invoice_links")
    op.drop_index("ix_tax_invoice_links_target_id", table_name="tax_invoice_links")
    op.drop_index("ix_tax_invoice_links_target_type", table_name="tax_invoice_links")
    op.drop_index("ix_tax_invoice_links_invoice_id", table_name="tax_invoice_links")
    op.drop_table("tax_invoice_links")

    op.drop_index("ix_tax_invoices_match_status", table_name="tax_invoices")
    op.drop_index("ix_tax_invoices_source_import_id", table_name="tax_invoices")
    op.drop_index("ix_tax_invoices_source_system", table_name="tax_invoices")
    op.drop_index("ix_tax_invoices_buyer_tax_id", table_name="tax_invoices")
    op.drop_index("ix_tax_invoices_buyer_name", table_name="tax_invoices")
    op.drop_index("ix_tax_invoices_seller_tax_id", table_name="tax_invoices")
    op.drop_index("ix_tax_invoices_seller_name", table_name="tax_invoices")
    op.drop_index("ix_tax_invoices_issue_date", table_name="tax_invoices")
    op.drop_index("ix_tax_invoices_status", table_name="tax_invoices")
    op.drop_index("ix_tax_invoices_invoice_number", table_name="tax_invoices")
    op.drop_index("ix_tax_invoices_invoice_code", table_name="tax_invoices")
    op.drop_index("ix_tax_invoices_direction", table_name="tax_invoices")
    op.drop_table("tax_invoices")

    op.drop_index("ix_tax_invoice_import_records_invoice_id", table_name="tax_invoice_import_records")
    op.drop_index("ix_tax_invoice_import_records_recognition_status", table_name="tax_invoice_import_records")
    op.drop_index("ix_tax_invoice_import_records_import_id", table_name="tax_invoice_import_records")
    op.drop_table("tax_invoice_import_records")

    op.drop_index("ix_tax_invoice_imports_status", table_name="tax_invoice_imports")
    op.drop_index("ix_tax_invoice_imports_period_month", table_name="tax_invoice_imports")
    op.drop_index("ix_tax_invoice_imports_period_year", table_name="tax_invoice_imports")
    op.drop_index("ix_tax_invoice_imports_source_system", table_name="tax_invoice_imports")
    op.drop_index("ix_tax_invoice_imports_sha256", table_name="tax_invoice_imports")
    op.drop_table("tax_invoice_imports")
