"""add structured multi-bank accounts to business partner master

Revision ID: partnerbank20260921
Revises: stocktake20260921
Create Date: 2026-09-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "partnerbank20260921"
down_revision = "stocktake20260921"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "business_partners",
        sa.Column(
            "bank_accounts",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    # 旧版只有一个主银行账户；原样迁入结构化列表，旧字段继续保留作兼容镜像。
    op.execute(
        """
        UPDATE business_partners
        SET bank_accounts = jsonb_build_array(
            jsonb_build_object(
                'bank_name', COALESCE(bank_name, ''),
                'account_no', COALESCE(bank_account_no, ''),
                'account_name', COALESCE(bank_account_name, ''),
                'is_primary', true
            )
        )
        WHERE COALESCE(bank_account_no, '') <> ''
        """
    )


def downgrade() -> None:
    op.drop_column("business_partners", "bank_accounts")
