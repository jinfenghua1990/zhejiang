"""upgrade existing warehouse master for configurable factory/B2C warehouses

Revision ID: b9d4f6a8c2e1
Revises: c4f8a2e6b9d1
"""

from alembic import op
import sqlalchemy as sa

revision = "b9d4f6a8c2e1"
down_revision = "c4f8a2e6b9d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 复用 Phase 0 已存在的 warehouses：吉客云仓和本地工厂仓使用同一主档。
    # 工厂仓没有吉客云 ID，因此放宽原字段为 nullable。
    op.alter_column(
        "warehouses",
        "jackyun_warehouse_id",
        existing_type=sa.String(length=64),
        nullable=True,
    )
    op.add_column("warehouses", sa.Column("code", sa.String(length=64), nullable=True))
    op.add_column("warehouses", sa.Column("purpose", sa.String(length=16), nullable=False, server_default="both"))
    op.add_column("warehouses", sa.Column("is_sellable", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("warehouses", sa.Column("note", sa.Text(), nullable=False, server_default=""))
    op.create_unique_constraint("uq_warehouses_code", "warehouses", ["code"])
    op.create_index("ix_warehouses_code", "warehouses", ["code"])
    op.create_index("ix_warehouses_name", "warehouses", ["name"])
    op.create_index("ix_warehouses_type", "warehouses", ["warehouse_type"])
    op.create_index("ix_warehouses_status", "warehouses", ["status"])

    # 现有吉客云仓给一个稳定本地编码。只在原角色为空时默认视为 B2C；已有
    # warehouse_type 不强行覆盖，避免破坏历史接口语义。吉客云仓默认用于正品。
    op.execute("UPDATE warehouses SET code = 'JKY-' || id WHERE code IS NULL")
    op.execute(
        """
        UPDATE warehouses
        SET warehouse_type = CASE WHEN COALESCE(warehouse_type, '') = '' THEN 'b2c' ELSE warehouse_type END,
            purpose = 'goods',
            is_sellable = true
        WHERE jackyun_warehouse_id IS NOT NULL
        """
    )

    # 本地工厂仓始终存在；B2C 仅在尚无 b2c 角色仓时补一个可配置占位仓。
    op.execute(
        """
        INSERT INTO warehouses
          (jackyun_warehouse_id, code, name, warehouse_type, purpose, is_sellable, status, note, raw)
        VALUES
          (NULL, 'FACTORY', '工厂仓库', 'factory', 'both', false, 'active',
           '默认工厂仓，可自行改名或新增更多工厂仓', '{}'::jsonb)
        ON CONFLICT (code) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO warehouses
          (jackyun_warehouse_id, code, name, warehouse_type, purpose, is_sellable, status, note, raw)
        SELECT NULL, 'B2C', 'B2C仓库', 'b2c', 'goods', true, 'active',
               '默认B2C发货仓；如吉客云已有真实仓，可在设置中绑定吉客云仓库ID', '{}'::jsonb
        WHERE NOT EXISTS (SELECT 1 FROM warehouses WHERE warehouse_type = 'b2c')
        ON CONFLICT (code) DO NOTHING
        """
    )

    op.add_column("consumable_receipts", sa.Column("warehouse_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_consumable_receipts_warehouse_id", "consumable_receipts", ["warehouse_id"])
    op.create_foreign_key(
        "fk_consumable_receipts_warehouse_id",
        "consumable_receipts",
        "warehouses",
        ["warehouse_id"],
        ["id"],
    )
    op.add_column("consumable_transactions", sa.Column("warehouse_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_consumable_transactions_warehouse_id", "consumable_transactions", ["warehouse_id"])
    op.create_foreign_key(
        "fk_consumable_transactions_warehouse_id",
        "consumable_transactions",
        "warehouses",
        ["warehouse_id"],
        ["id"],
    )

    # factory 的历史语义明确，可安全归仓；旧 own 不能武断等同 B2C，因此不回填。
    op.execute(
        """
        UPDATE consumable_receipts
        SET warehouse_id = (SELECT id FROM warehouses WHERE code = 'FACTORY' LIMIT 1)
        WHERE location = 'factory' AND warehouse_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE consumable_transactions
        SET warehouse_id = (SELECT id FROM warehouses WHERE code = 'FACTORY' LIMIT 1)
        WHERE location = 'factory' AND warehouse_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_consumable_transactions_warehouse_id", "consumable_transactions", type_="foreignkey")
    op.drop_index("ix_consumable_transactions_warehouse_id", table_name="consumable_transactions")
    op.drop_column("consumable_transactions", "warehouse_id")
    op.drop_constraint("fk_consumable_receipts_warehouse_id", "consumable_receipts", type_="foreignkey")
    op.drop_index("ix_consumable_receipts_warehouse_id", table_name="consumable_receipts")
    op.drop_column("consumable_receipts", "warehouse_id")

    # 恢复旧模型前先移除没有吉客云 ID 的本地仓；历史吉客云仓保留。
    op.execute("DELETE FROM warehouses WHERE jackyun_warehouse_id IS NULL")
    op.drop_index("ix_warehouses_status", table_name="warehouses")
    op.drop_index("ix_warehouses_type", table_name="warehouses")
    op.drop_index("ix_warehouses_name", table_name="warehouses")
    op.drop_index("ix_warehouses_code", table_name="warehouses")
    op.drop_constraint("uq_warehouses_code", "warehouses", type_="unique")
    op.drop_column("warehouses", "note")
    op.drop_column("warehouses", "is_sellable")
    op.drop_column("warehouses", "purpose")
    op.drop_column("warehouses", "code")
    op.alter_column(
        "warehouses",
        "jackyun_warehouse_id",
        existing_type=sa.String(length=64),
        nullable=False,
    )
