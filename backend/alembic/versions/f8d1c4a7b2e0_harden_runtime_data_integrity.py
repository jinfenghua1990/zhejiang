"""harden runtime data integrity

Revision ID: f8d1c4a7b2e0
Revises: e7b4c2d19a01
Create Date: 2026-09-02

- 一笔银行流水只允许一个 confirmed 匹配
- 财务包版本、首次成功邮件、原始 API 样本加数据库完整性约束
- 校正旧版把吉客云 permission-denied 空响应误记为 success 的历史运行记录
"""

from alembic import op
import sqlalchemy as sa


revision = "f8d1c4a7b2e0"
down_revision = "e7b4c2d19a01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_finance_delivery_package_version",
        "finance_delivery_packages",
        ["period_id", "version"],
    )
    op.create_unique_constraint(
        "uq_raw_payload_provider_digest",
        "raw_api_payloads",
        ["provider", "request_digest"],
    )
    op.create_index(
        "uq_reconciliation_matches_confirmed_txn",
        "reconciliation_matches",
        ["txn_id"],
        unique=True,
        postgresql_where=sa.text("status = 'confirmed'"),
    )
    op.create_index(
        "uq_email_delivery_first_sent",
        "email_delivery_logs",
        ["package_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'first' AND status = 'sent'"),
    )

    # 旧适配器没有识别 MCP content 内的 subCode=0130000609，故空 stats 的业务任务被标成 success。
    # 保留记录和时间，只校正状态与说明；connection_test 仅证明传输层，不能一起改写。
    op.execute(
        """
        UPDATE sync_jobs
        SET status = 'failed',
            error_summary = '历史记录校正：旧版本未识别吉客云业务权限拒绝；未写入业务数据'
        WHERE provider = 'jackyun'
          AND job_type <> 'connection_test'
          AND status = 'success'
          AND COALESCE(stats, '{}'::jsonb) = '{}'::jsonb
        """
    )
    op.execute(
        """
        UPDATE sync_logs
        SET level = 'warn',
            message = '历史记录校正：旧版本将吉客云业务权限拒绝误记为完成；未写入业务数据'
        WHERE provider = 'jackyun'
          AND sync_job_id IS NULL
          AND message LIKE 'sync % 完成'
        """
    )


def downgrade() -> None:
    op.drop_index("uq_email_delivery_first_sent", table_name="email_delivery_logs")
    op.drop_index("uq_reconciliation_matches_confirmed_txn", table_name="reconciliation_matches")
    op.drop_constraint("uq_raw_payload_provider_digest", "raw_api_payloads", type_="unique")
    op.drop_constraint("uq_finance_delivery_package_version", "finance_delivery_packages", type_="unique")
