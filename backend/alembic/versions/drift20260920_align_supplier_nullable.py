"""align supplier nullable schema with ORM

Revision ID: drift20260920
Revises: finp20260920
Create Date: 2026-09-20
"""

from alembic import op
import sqlalchemy as sa


revision = "drift20260920"
down_revision = "finp20260920"
branch_labels = None
depends_on = None


def _ensure_fk(
    name: str,
    source_table: str,
    target_table: str,
    source_columns: list[str],
    target_columns: list[str],
    *,
    ondelete: str | None = None,
    operations=op,
) -> None:
    """Create the expected FK and tolerate a pre-existing identical constraint.

    Production may already contain the named FK even when this Alembic revision
    is still pending. PostgreSQL raises DuplicateObject in that case, so catch
    that database-native condition inside one DO block, then validate the
    existing constraint from pg_constraint before continuing.
    """
    if len(source_columns) != 1 or len(target_columns) != 1:
        raise RuntimeError("finance delivery migration only supports single-column FKs")

    source_column = source_columns[0]
    target_column = target_columns[0]
    delete_sql = f" ON DELETE {ondelete}" if ondelete else ""

    operations.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                BEGIN
                    ALTER TABLE {source_table}
                    ADD CONSTRAINT {name}
                    FOREIGN KEY ({source_column})
                    REFERENCES {target_table} ({target_column}){delete_sql};
                EXCEPTION
                    WHEN duplicate_object THEN
                        NULL;
                END;
            END
            $$;
            """
        )
    )

    row = operations.get_bind().execute(
        sa.text(
            """
            SELECT
                ARRAY(
                    SELECT a.attname
                    FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
                    JOIN pg_attribute AS a
                      ON a.attrelid = c.conrelid
                     AND a.attnum = k.attnum
                    ORDER BY k.ord
                ) AS source_columns,
                c.confrelid::regclass::text AS target_table,
                ARRAY(
                    SELECT a.attname
                    FROM unnest(c.confkey) WITH ORDINALITY AS k(attnum, ord)
                    JOIN pg_attribute AS a
                      ON a.attrelid = c.confrelid
                     AND a.attnum = k.attnum
                    ORDER BY k.ord
                ) AS target_columns,
                c.confdeltype
            FROM pg_constraint AS c
            WHERE c.conrelid = to_regclass(:source_table)
              AND c.conname = :constraint_name
              AND c.contype = 'f'
            """
        ),
        {"source_table": source_table, "constraint_name": name},
    ).mappings().first()

    expected_delete = {"CASCADE": "c", "RESTRICT": "r", "SET NULL": "n", "SET DEFAULT": "d"}.get(
        str(ondelete or "").upper(),
        "a",
    )
    if (
        row is None
        or list(row["source_columns"] or []) != source_columns
        or str(row["target_table"] or "").split(".")[-1] != target_table
        or list(row["target_columns"] or []) != target_columns
        or str(row["confdeltype"] or "") != expected_delete
    ):
        raise RuntimeError(
            f"constraint {name} exists but does not match the expected "
            f"{source_table}({source_column}) -> {target_table}({target_column}) definition"
        )


def _drop_fk_if_exists(name: str, table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}"))

def upgrade() -> None:
    # 旧模型已提升到 10 位小数，但迁移仍停在 4 位；使用 24,10 保留原有
    # 14 位整数容量，同时获得 10 位小数精度。
    op.alter_column(
        "consumable_purchase_items",
        "unit_cost",
        existing_type=sa.Numeric(18, 4),
        type_=sa.Numeric(24, 10),
        existing_nullable=False,
    )
    op.alter_column(
        "consumable_transactions",
        "unit_cost",
        existing_type=sa.Numeric(18, 4),
        type_=sa.Numeric(24, 10),
        existing_nullable=True,
    )

    # FinanceDeliveryFile 模型声明了两个 FK，但 phase0 迁移只建了列与索引。
    # 正式补上约束；如果生产库存在孤儿行，迁移会明确失败而不是静默删除数据。
    _ensure_fk(
        "fk_fdf_package",
        "finance_delivery_files",
        "finance_delivery_packages",
        ["package_id"],
        ["id"],
        ondelete="CASCADE",
    )
    _ensure_fk(
        "fk_fdf_archive",
        "finance_delivery_files",
        "archive_files",
        ["archive_file_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 旧迁移把这些字段建成 nullable=True，而 ORM 一直按字符串/整数非空字段使用。
    # 先回填历史 NULL，再收紧约束，避免 ALTER TABLE 因存量数据失败。
    op.execute("UPDATE suppliers SET bank_name = '' WHERE bank_name IS NULL")
    op.execute("UPDATE suppliers SET bank_account_no = '' WHERE bank_account_no IS NULL")
    op.execute("UPDATE suppliers SET bank_account_name = '' WHERE bank_account_name IS NULL")
    op.execute("UPDATE suppliers SET tax_invoice_count = 0 WHERE tax_invoice_count IS NULL")

    op.alter_column(
        "suppliers",
        "bank_name",
        existing_type=sa.String(length=128),
        nullable=False,
    )
    op.alter_column(
        "suppliers",
        "bank_account_no",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.alter_column(
        "suppliers",
        "bank_account_name",
        existing_type=sa.String(length=256),
        nullable=False,
    )
    op.alter_column(
        "suppliers",
        "tax_invoice_count",
        existing_type=sa.Integer(),
        existing_server_default=sa.text("'0'"),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "suppliers",
        "tax_invoice_count",
        existing_type=sa.Integer(),
        existing_server_default=sa.text("'0'"),
        nullable=True,
    )
    op.alter_column(
        "suppliers",
        "bank_account_name",
        existing_type=sa.String(length=256),
        nullable=True,
    )
    op.alter_column(
        "suppliers",
        "bank_account_no",
        existing_type=sa.String(length=64),
        nullable=True,
    )
    op.alter_column(
        "suppliers",
        "bank_name",
        existing_type=sa.String(length=128),
        nullable=True,
    )

    _drop_fk_if_exists("fk_fdf_archive", "finance_delivery_files")
    _drop_fk_if_exists("fk_fdf_package", "finance_delivery_files")

    op.alter_column(
        "consumable_transactions",
        "unit_cost",
        existing_type=sa.Numeric(24, 10),
        type_=sa.Numeric(18, 4),
        existing_nullable=True,
    )
    op.alter_column(
        "consumable_purchase_items",
        "unit_cost",
        existing_type=sa.Numeric(24, 10),
        type_=sa.Numeric(18, 4),
        existing_nullable=False,
    )
