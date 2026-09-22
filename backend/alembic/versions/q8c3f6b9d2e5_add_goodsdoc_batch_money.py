"""add batch/expiry and money dimensions to jackyun goods documents

Revision ID: q8c3f6b9d2e5
Revises: p7b2f8d4c6a9
Create Date: 2026-09-03

承接吉客云客户端导出文件（采购入库申请单）里 API 接口不含的维度：
- 主档金额：total_amount（入库金额）/ total_fee（入库费用）
- 明细数量：apply_quantity（申请数量）/ remain_quantity / return_quantity
- 明细金额：unit_price_tax / unit_price_notax / amount_tax / amount_notax
- 批次效期：batch_no / production_lot / production_date / expiry_date /
  shelf_life / shelf_life_unit / manufacturer / approval_no
- 其他：spec（规格）/ goods_status（货品入库状态）

全部 nullable 或带默认空串，API 同步链路不受影响（保持为空），
由文件导入通道按 Excel 维度写入。可安全重跑。
"""
from alembic import op
import sqlalchemy as sa


revision = "q8c3f6b9d2e5"
down_revision = "p7b2f8d4c6a9"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(18, 4)
QUANTITY = sa.Numeric(18, 4)


def upgrade() -> None:
    op.add_column("jackyun_goods_documents", sa.Column("total_amount", MONEY, nullable=True))
    op.add_column("jackyun_goods_documents", sa.Column("total_fee", MONEY, nullable=True))

    item_cols = [
        sa.Column("spec", sa.String(length=128), nullable=False, server_default=sa.text("''")),
        sa.Column("apply_quantity", QUANTITY, nullable=True),
        sa.Column("remain_quantity", QUANTITY, nullable=True),
        sa.Column("return_quantity", QUANTITY, nullable=True),
        sa.Column("unit_price_tax", MONEY, nullable=True),
        sa.Column("unit_price_notax", MONEY, nullable=True),
        sa.Column("amount_tax", MONEY, nullable=True),
        sa.Column("amount_notax", MONEY, nullable=True),
        sa.Column("batch_no", sa.String(length=128), nullable=False, server_default=sa.text("''")),
        sa.Column("production_lot", sa.String(length=128), nullable=False, server_default=sa.text("''")),
        sa.Column("production_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expiry_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shelf_life", sa.String(length=32), nullable=False, server_default=sa.text("''")),
        sa.Column("shelf_life_unit", sa.String(length=16), nullable=False, server_default=sa.text("''")),
        sa.Column("manufacturer", sa.String(length=256), nullable=False, server_default=sa.text("''")),
        sa.Column("approval_no", sa.String(length=128), nullable=False, server_default=sa.text("''")),
        sa.Column("goods_status", sa.String(length=64), nullable=False, server_default=sa.text("''")),
    ]
    for col in item_cols:
        op.add_column("jackyun_goods_document_items", col)
    op.create_index(op.f("ix_jackyun_goods_document_items_batch_no"), "jackyun_goods_document_items", ["batch_no"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_jackyun_goods_document_items_batch_no"), table_name="jackyun_goods_document_items")
    for name in (
        "goods_status", "approval_no", "manufacturer", "shelf_life_unit", "shelf_life",
        "expiry_date", "production_date", "production_lot", "batch_no",
        "amount_notax", "amount_tax", "unit_price_notax", "unit_price_tax",
        "return_quantity", "remain_quantity", "apply_quantity", "spec",
    ):
        op.drop_column("jackyun_goods_document_items", name)
    op.drop_column("jackyun_goods_documents", "total_fee")
    op.drop_column("jackyun_goods_documents", "total_amount")
