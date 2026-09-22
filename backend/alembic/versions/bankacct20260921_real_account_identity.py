"""keep internal bank code separate from the real account number

Revision ID: bankacct20260921
Revises: bankraw20260921
"""

from alembic import op
import sqlalchemy as sa


revision = "bankacct20260921"
down_revision = "bankraw20260921"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bank_accounts",
        sa.Column("internal_code", sa.String(length=64), nullable=True),
    )

    # 旧生产记录把内部别名当成了 account_no。迁移时保留该别名作为内部编号，
    # 把同一账户的真实账号写回 account_no；不删除任何账户或流水。
    op.execute(
        sa.text(
            """
            UPDATE bank_accounts
            SET internal_code = 'ZJRC-001'
            WHERE account_no = 'ZJRC-001'
              AND (internal_code IS NULL OR internal_code = '')
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE bank_accounts
            SET account_no = '201000260611394',
                account_name = '浙江柴本网络科技有限公司'
            WHERE internal_code = 'ZJRC-001'
              AND account_no = 'ZJRC-001'
              AND NOT EXISTS (
                  SELECT 1 FROM bank_accounts
                  WHERE account_no = '201000260611394'
              )
            """
        )
    )

    # 极少数环境可能已经有真实账号行：把历史流水指向真实行，旧别名行仍保留。
    op.execute(
        sa.text(
            """
            DO $$
            DECLARE
                alias_id bigint;
                real_id bigint;
            BEGIN
                SELECT id INTO alias_id FROM bank_accounts WHERE account_no = 'ZJRC-001';
                SELECT id INTO real_id FROM bank_accounts WHERE account_no = '201000260611394';
                IF alias_id IS NOT NULL AND real_id IS NOT NULL AND alias_id <> real_id THEN
                    UPDATE bank_transactions SET account_id = real_id WHERE account_id = alias_id;
                    UPDATE bank_accounts
                    SET internal_code = 'ZJRC-001'
                    WHERE id = real_id;
                    UPDATE bank_accounts
                    SET internal_code = 'ZJRC-001-LEGACY'
                    WHERE id = alias_id;
                END IF;
            END $$;
            """
        )
    )

    op.create_unique_constraint(
        "uq_bank_accounts_internal_code",
        "bank_accounts",
        ["internal_code"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_bank_accounts_internal_code", "bank_accounts", type_="unique")
    op.drop_column("bank_accounts", "internal_code")
