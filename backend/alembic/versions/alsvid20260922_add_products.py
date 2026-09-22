"""add ALSVID foreign-trade product platforms and products

Revision ID: alsvid20260922_products
Revises: fincv20260922_currency
Create Date: 2026-09-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "alsvid20260922_products"
down_revision = "fincv20260922_currency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "foreign_trade_product_platforms",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("brand", sa.String(128), nullable=False, server_default="ALSVID"),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("name_en", sa.String(128), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("raw", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("brand", "code", name="uq_foreign_trade_product_platform_brand_code"),
    )
    op.create_index("ix_foreign_trade_product_platform_brand", "foreign_trade_product_platforms", ["brand"])
    op.create_index("ix_foreign_trade_product_platform_order", "foreign_trade_product_platforms", ["display_order"])

    op.create_table(
        "foreign_trade_products",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("brand", sa.String(128), nullable=False, server_default="ALSVID"),
        sa.Column("platform_code", sa.String(32), nullable=False),
        sa.Column("model_code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("name_en", sa.String(256), nullable=False, server_default=""),
        sa.Column("sku_id", sa.BigInteger(), nullable=True),
        sa.Column("external_sku", sa.String(128), nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("countries", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("currency", sa.String(8), nullable=False, server_default="EUR"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["sku_id"], ["product_skus.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("brand", "model_code", name="uq_foreign_trade_products_brand_model"),
    )
    op.create_index("ix_foreign_trade_products_brand_platform", "foreign_trade_products", ["brand", "platform_code"])
    op.create_index("ix_foreign_trade_products_status", "foreign_trade_products", ["status"])
    op.create_index("ix_foreign_trade_products_sku_id", "foreign_trade_products", ["sku_id"])

    platform_table = sa.table(
        "foreign_trade_product_platforms",
        sa.column("brand", sa.String(128)),
        sa.column("code", sa.String(32)),
        sa.column("name", sa.String(128)),
        sa.column("name_en", sa.String(128)),
        sa.column("description", sa.Text()),
        sa.column("display_order", sa.Integer()),
    )
    op.bulk_insert(platform_table, [
        {"brand": "ALSVID", "code": "FC1", "name": "折叠旗舰", "name_en": "Folding Flagship", "description": "折叠旗舰平台，优先承载 ALSVID 的高端折叠产品。", "display_order": 10},
        {"brand": "ALSVID", "code": "FT1", "name": "胖胎", "name_en": "Fat Tire", "description": "Fat Tire 胖胎平台，面向复杂路况与更强通过性。", "display_order": 20},
        {"brand": "ALSVID", "code": "CT1", "name": "都市", "name_en": "City", "description": "City 都市平台，面向城市通勤与日常骑行。", "display_order": 30},
        {"brand": "ALSVID", "code": "GT1", "name": "长途", "name_en": "Gravel / Touring", "description": "Gravel / Touring 长途平台，面向长距离与多路面骑行。", "display_order": 40},
    ])

    product_table = sa.table(
        "foreign_trade_products",
        sa.column("brand", sa.String(128)),
        sa.column("platform_code", sa.String(32)),
        sa.column("model_code", sa.String(64)),
        sa.column("name", sa.String(256)),
        sa.column("name_en", sa.String(256)),
        sa.column("external_sku", sa.String(128)),
        sa.column("status", sa.String(24)),
        sa.column("countries", postgresql.JSONB()),
        sa.column("currency", sa.String(8)),
        sa.column("note", sa.Text()),
    )
    op.bulk_insert(product_table, [
        {"brand": "ALSVID", "platform_code": "FC1", "model_code": "FC1", "name": "折叠旗舰", "name_en": "Folding Flagship", "external_sku": "", "status": "planned", "countries": ["DE", "AT"], "currency": "EUR", "note": "技术平台产品主档，待补充具体配置、价格与中台 SKU。"},
        {"brand": "ALSVID", "platform_code": "FT1", "model_code": "FT1", "name": "胖胎", "name_en": "Fat Tire", "external_sku": "", "status": "planned", "countries": ["DE", "AT"], "currency": "EUR", "note": "技术平台产品主档，待补充具体配置、价格与中台 SKU。"},
        {"brand": "ALSVID", "platform_code": "CT1", "model_code": "CT1", "name": "都市", "name_en": "City", "external_sku": "", "status": "planned", "countries": ["DE", "AT"], "currency": "EUR", "note": "技术平台产品主档，待补充具体配置、价格与中台 SKU。"},
        {"brand": "ALSVID", "platform_code": "GT1", "model_code": "GT1", "name": "长途", "name_en": "Gravel / Touring", "external_sku": "", "status": "planned", "countries": ["DE", "AT"], "currency": "EUR", "note": "技术平台产品主档，待补充具体配置、价格与中台 SKU。"},
    ])


def downgrade() -> None:
    op.drop_index("ix_foreign_trade_products_sku_id", table_name="foreign_trade_products")
    op.drop_index("ix_foreign_trade_products_status", table_name="foreign_trade_products")
    op.drop_index("ix_foreign_trade_products_brand_platform", table_name="foreign_trade_products")
    op.drop_table("foreign_trade_products")
    op.drop_index("ix_foreign_trade_product_platform_order", table_name="foreign_trade_product_platforms")
    op.drop_index("ix_foreign_trade_product_platform_brand", table_name="foreign_trade_product_platforms")
    op.drop_table("foreign_trade_product_platforms")
