"""add procurement chain links and invoice verification fields

Revision ID: m4a7c2e9b5f1
Revises: k3b8d1f5a7c9
Create Date: 2026-09-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "m4a7c2e9b5f1"
down_revision: Union[str, None] = "k3b8d1f5a7c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) 吉客云入库单预留供应商字段（接口受限时为空，恢复后由同步补齐）
    op.add_column("jackyun_goods_documents", sa.Column("supplier_name", sa.String(length=256), nullable=False, server_default=""))
    op.create_index(op.f("ix_jackyun_goods_documents_supplier_name"), "jackyun_goods_documents", ["supplier_name"], unique=False)

    # 2) 税务发票加认证（勾选抵扣）字段
    op.add_column("tax_invoices", sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("tax_invoices", sa.Column("verified_month", sa.String(length=16), nullable=False, server_default=""))
    op.add_column("tax_invoices", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_tax_invoices_verified"), "tax_invoices", ["verified"], unique=False)

    # 3) 采购链路关联表：1688 订单 ↔ 入库单/结算单
    op.create_table(
        "procurement_chain_links",
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("match_method", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["alibaba1688_orders.id"], name="fk_procurement_chain_links_order"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "target_type", "target_id", name="uq_procurement_chain_link_target"),
    )
    op.create_index(op.f("ix_procurement_chain_links_order_id"), "procurement_chain_links", ["order_id"], unique=False)
    op.create_index(op.f("ix_procurement_chain_links_target_type"), "procurement_chain_links", ["target_type"], unique=False)
    op.create_index(op.f("ix_procurement_chain_links_target_id"), "procurement_chain_links", ["target_id"], unique=False)
    op.create_index(op.f("ix_procurement_chain_links_confirmed"), "procurement_chain_links", ["confirmed"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_procurement_chain_links_confirmed"), table_name="procurement_chain_links")
    op.drop_index(op.f("ix_procurement_chain_links_target_id"), table_name="procurement_chain_links")
    op.drop_index(op.f("ix_procurement_chain_links_target_type"), table_name="procurement_chain_links")
    op.drop_index(op.f("ix_procurement_chain_links_order_id"), table_name="procurement_chain_links")
    op.drop_table("procurement_chain_links")
    op.drop_index(op.f("ix_tax_invoices_verified"), table_name="tax_invoices")
    op.drop_column("tax_invoices", "verified_at")
    op.drop_column("tax_invoices", "verified_month")
    op.drop_column("tax_invoices", "verified")
    op.drop_index(op.f("ix_jackyun_goods_documents_supplier_name"), table_name="jackyun_goods_documents")
    op.drop_column("jackyun_goods_documents", "supplier_name")
