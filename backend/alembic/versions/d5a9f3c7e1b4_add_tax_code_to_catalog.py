"""product_skus / consumables: tax_code（税收分类编码）

货品档案增加开票用税收分类编码（19 位商品和服务税收分类编码，
兼容旧 10 位简称）。正品与耗材两张主档同口径添加，默认空串。

Revision ID: d5a9f3c7e1b4
Revises: c4f8a2e6b9d1
"""

from alembic import op
import sqlalchemy as sa


revision = "d5a9f3c7e1b4"
down_revision = "c4f8a2e6b9d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("product_skus", "consumables"):
        op.add_column(
            table,
            sa.Column(
                "tax_code",
                sa.String(length=32),
                nullable=False,
                server_default="",
                comment="税收分类编码（开票用，19 位；兼容旧 10 位简称）",
            ),
        )


def downgrade() -> None:
    for table in ("product_skus", "consumables"):
        op.drop_column(table, "tax_code")
