"""normalize existing catalog tax-category links

This is a guarded data repair for the local catalog:

* link a direct tax code to a single enabled rule only when the code is
  unambiguous;
* preserve the existing coffee rule for coffee records that were carrying
  the biscuit code;
* add the biscuit rule only when the matching Pocky catalog row exists.

Rows without a reliable rule remain direct-code exceptions and are not
classified by inference.
"""

from alembic import op
import sqlalchemy as sa


revision = "a6c8e0f2b4d1"
down_revision = "f5a8c2e4b6d0"
branch_labels = None
depends_on = None


BISCUIT_CODE = "1030201030000000000"


def upgrade() -> None:
    conn = op.get_bind()

    # The current catalog contains an explicitly configured coffee rule. Four
    # coffee SKUs had the public biscuit code copied onto them; align them to
    # the existing user-maintained coffee rule, without touching the Pocky row.
    conn.execute(
        sa.text(
            """
            UPDATE product_skus AS sku
            SET tax_category_rule_id = rule.id,
                tax_code = rule.tax_code
            FROM tax_accounting_category_rules AS rule
            WHERE rule.category_name = '软饮料'
              AND rule.item_name = '咖啡'
              AND rule.enabled IS TRUE
              AND NULLIF(BTRIM(rule.tax_code), '') IS NOT NULL
              AND sku.tax_category_rule_id IS NULL
              AND BTRIM(sku.tax_code) = CAST(:biscuit_code AS TEXT)
              AND sku.sku_name ILIKE '%咖啡%'
              AND sku.sku_name NOT ILIKE '%百力滋%'
            """
        ),
        {"biscuit_code": BISCUIT_CODE},
    )

    # Only create this default rule when the known biscuit catalog row is
    # present. Existing user rules always win through the unique constraint.
    conn.execute(
        sa.text(
            """
            INSERT INTO tax_accounting_category_rules
                (category_name, item_name, tax_code, match_keyword, match_mode,
                 priority, enabled, note, created_by, updated_by)
            SELECT '焙烤食品', '饼干', CAST(:biscuit_code AS VARCHAR(32)), '百力滋', 'contains',
                   100, TRUE,
                   '按货品档案百力滋商品归类；官方税务分类优先。',
                   'migration', 'migration'
            WHERE EXISTS (
                SELECT 1
                FROM product_skus
                WHERE BTRIM(tax_code) = CAST(:biscuit_code AS TEXT)
                  AND sku_name ILIKE '%百力滋%'
            )
            ON CONFLICT (category_name, item_name) DO NOTHING
            """
        ),
        {"biscuit_code": BISCUIT_CODE},
    )

    # Link rows whose direct code maps to exactly one enabled rule. This is
    # deliberately conservative: duplicate rules for one code stay manual.
    for table in ("product_skus", "consumables"):
        conn.execute(
            sa.text(
                f"""
                WITH unique_rules AS (
                    SELECT BTRIM(tax_code) AS tax_code, MIN(id) AS rule_id
                    FROM tax_accounting_category_rules
                    WHERE enabled IS TRUE
                      AND NULLIF(BTRIM(tax_code), '') IS NOT NULL
                    GROUP BY BTRIM(tax_code)
                    HAVING COUNT(*) = 1
                )
                UPDATE {table} AS catalog
                SET tax_category_rule_id = unique_rules.rule_id
                FROM unique_rules
                WHERE catalog.tax_category_rule_id IS NULL
                  AND BTRIM(catalog.tax_code) = unique_rules.tax_code
                """
            )
        )


def downgrade() -> None:
    # This migration repairs existing business data and intentionally has no
    # safe automatic rollback: a later manual edit could have changed the
    # same tax code or association.
    pass
