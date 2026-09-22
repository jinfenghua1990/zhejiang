"""Allow manually created procurement workflows to link original business records.

Revision ID: s1e5f8a2b4c6
Revises: r9d4e7f1a3b6
"""
from alembic import op
import sqlalchemy as sa

revision = "s1e5f8a2b4c6"
down_revision = "r9d4e7f1a3b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("procurement_chain_links", sa.Column("external_po_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_procurement_chain_workflow", "procurement_chain_links", "external_purchase_orders", ["external_po_id"], ["id"])
    op.create_index("ix_procurement_chain_links_external_po_id", "procurement_chain_links", ["external_po_id"])
    op.alter_column("procurement_chain_links", "order_id", nullable=True)
    op.create_unique_constraint("uq_procurement_workflow_link_target", "procurement_chain_links", ["external_po_id", "target_type", "target_id"])
    op.create_check_constraint("ck_procurement_link_source", "procurement_chain_links", "(order_id IS NOT NULL) <> (external_po_id IS NOT NULL)")


def downgrade() -> None:
    # 不丢弃用户已经建立的手工订单关联；回滚前须明确迁移这些记录。
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM procurement_chain_links WHERE external_po_id IS NOT NULL")).scalar()
    if count:
        raise RuntimeError("存在手工采购订单关联，禁止有损回滚")
    op.drop_constraint("ck_procurement_link_source", "procurement_chain_links", type_="check")
    op.drop_constraint("uq_procurement_workflow_link_target", "procurement_chain_links", type_="unique")
    op.alter_column("procurement_chain_links", "order_id", nullable=False)
    op.drop_index("ix_procurement_chain_links_external_po_id", table_name="procurement_chain_links")
    op.drop_constraint("fk_procurement_chain_workflow", "procurement_chain_links", type_="foreignkey")
    op.drop_column("procurement_chain_links", "external_po_id")
