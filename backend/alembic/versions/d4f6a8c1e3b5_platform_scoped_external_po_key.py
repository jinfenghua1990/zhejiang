"""external_purchase_orders: 渠道 + 外部订单号联合唯一

同一个数字订单号可能分别存在于 1688 / 淘宝 / 拼多多。原来的单列唯一约束会把
跨渠道同号订单误认为同一笔采购。迁移只改变唯一键，不改任何历史业务行。

Revision ID: d4f6a8c1e3b5
Revises: ca37bfabdb8c
"""

from alembic import op
import sqlalchemy as sa


revision = "d4f6a8c1e3b5"
down_revision = "ca37bfabdb8c"
branch_labels = None
depends_on = None


def _single_order_no_unique_name() -> str | None:
    inspector = sa.inspect(op.get_bind())
    for constraint in inspector.get_unique_constraints("external_purchase_orders"):
        columns = constraint.get("column_names") or []
        if columns == ["external_order_id"]:
            return constraint.get("name")
    return None


def upgrade() -> None:
    old_name = _single_order_no_unique_name()
    if old_name:
        op.drop_constraint(old_name, "external_purchase_orders", type_="unique")
    op.create_unique_constraint(
        "uq_external_purchase_platform_order",
        "external_purchase_orders",
        ["platform", "external_order_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_external_purchase_platform_order",
        "external_purchase_orders",
        type_="unique",
    )
    # 只有没有跨渠道同号数据时才能安全退回旧约束；若已产生同号订单，数据库会明确拒绝
    # downgrade，避免静默删改用户数据。
    op.create_unique_constraint(
        "external_purchase_orders_external_order_id_key",
        "external_purchase_orders",
        ["external_order_id"],
    )
