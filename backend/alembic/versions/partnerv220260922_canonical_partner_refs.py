"""canonical business partner references across operational facts

Revision ID: partnerv220260922
Revises: partnerbank20260921
Create Date: 2026-09-22

BusinessPartner becomes the canonical identity root. Raw source names/tax numbers/accounts
remain untouched for audit, while operational facts get nullable FK references to the
resolved partner. Existing confirmed BusinessPartnerLink rows are materialized into
these columns during migration.
"""

from alembic import op
import sqlalchemy as sa


revision = "partnerv220260922"
down_revision = "partnerbank20260921"
branch_labels = None
depends_on = None


def _partner_fk(table: str, column: str, fk_name: str) -> None:
    op.add_column(table, sa.Column(column, sa.BigInteger(), nullable=True))
    op.create_index(f"ix_{table}_{column}", table, [column], unique=False)
    op.create_foreign_key(
        fk_name,
        table,
        "business_partners",
        [column],
        ["id"],
        ondelete="SET NULL",
    )


def upgrade() -> None:
    op.create_table(
        "business_partner_roles",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("partner_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="system"),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["partner_id"], ["business_partners.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("partner_id", "role", name="uq_business_partner_role"),
    )
    op.create_index("ix_business_partner_roles_partner_id", "business_partner_roles", ["partner_id"])
    op.create_index("ix_business_partner_roles_role", "business_partner_roles", ["role"])

    op.create_table(
        "business_partner_bank_accounts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("partner_id", sa.BigInteger(), nullable=False),
        sa.Column("bank_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("account_no", sa.String(length=128), nullable=False),
        sa.Column("normalized_account_no", sa.String(length=128), nullable=False),
        sa.Column("account_name", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="system"),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["partner_id"], ["business_partners.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("partner_id", "normalized_account_no", name="uq_business_partner_bank_account"),
    )
    op.create_index("ix_business_partner_bank_accounts_partner_id", "business_partner_bank_accounts", ["partner_id"])
    op.create_index("ix_business_partner_bank_accounts_normalized_account_no", "business_partner_bank_accounts", ["normalized_account_no"])
    op.create_index("ix_business_partner_bank_accounts_status", "business_partner_bank_accounts", ["status"])

    _partner_fk("suppliers", "partner_id", "fk_suppliers_partner")
    _partner_fk("external_purchase_orders", "supplier_partner_id", "fk_external_purchase_orders_supplier_partner")
    _partner_fk("alibaba1688_orders", "supplier_partner_id", "fk_alibaba1688_orders_supplier_partner")
    _partner_fk("jackyun_purchase_orders", "supplier_partner_id", "fk_jackyun_purchase_orders_supplier_partner")
    _partner_fk("jackyun_purchase_settlements", "supplier_partner_id", "fk_jackyun_purchase_settlements_supplier_partner")
    _partner_fk("jackyun_purchase_returns", "supplier_partner_id", "fk_jackyun_purchase_returns_supplier_partner")
    _partner_fk("jackyun_goods_documents", "supplier_partner_id", "fk_jackyun_goods_documents_supplier_partner")
    _partner_fk("consumable_purchases", "supplier_partner_id", "fk_consumable_purchases_supplier_partner")
    _partner_fk("jky_web_stockin_orders", "supplier_partner_id", "fk_jky_web_stockin_orders_supplier_partner")
    _partner_fk("tax_invoices", "seller_partner_id", "fk_tax_invoices_seller_partner")
    _partner_fk("tax_invoices", "buyer_partner_id", "fk_tax_invoices_buyer_partner")
    _partner_fk("bank_transactions", "counterparty_partner_id", "fk_bank_transactions_counterparty_partner")
    _partner_fk("jky_web_sales_orders", "customer_partner_id", "fk_jky_web_sales_orders_customer_partner")
    _partner_fk("sales_orders", "customer_partner_id", "fk_sales_orders_customer_partner")

    # Existing roles are deterministic master data; normalize them into a relation table.
    op.execute(
        """
        INSERT INTO business_partner_roles (partner_id, role, source, confirmed)
        SELECT bp.id, role_value, 'legacy_roles', true
        FROM business_partners bp
        CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(bp.roles, '[]'::jsonb)) AS role_value
        WHERE role_value IN ('supplier', 'customer', 'counterparty')
        ON CONFLICT (partner_id, role) DO NOTHING
        """
    )

    # Preserve all already-known bank accounts from structured JSON.
    op.execute(
        """
        INSERT INTO business_partner_bank_accounts
            (partner_id, bank_name, account_no, normalized_account_no, account_name,
             is_primary, status, source, verified)
        SELECT
            bp.id,
            COALESCE(item->>'bank_name', item->>'bankName', ''),
            COALESCE(item->>'account_no', item->>'accountNo', ''),
            regexp_replace(
                upper(COALESCE(item->>'account_no', item->>'accountNo', '')),
                '[[:space:]-]+', '', 'g'
            ),
            COALESCE(item->>'account_name', item->>'accountName', ''),
            COALESCE((item->>'is_primary')::boolean, (item->>'isPrimary')::boolean, false),
            'active',
            'legacy_json',
            true
        FROM business_partners bp
        CROSS JOIN LATERAL jsonb_array_elements(COALESCE(bp.bank_accounts, '[]'::jsonb)) AS item
        WHERE COALESCE(item->>'account_no', item->>'accountNo', '') <> ''
        ON CONFLICT (partner_id, normalized_account_no) DO NOTHING
        """
    )
    # Identifier-only accounts are also retained; details can be enriched later.
    op.execute(
        """
        INSERT INTO business_partner_bank_accounts
            (partner_id, bank_name, account_no, normalized_account_no, account_name,
             is_primary, status, source, verified)
        SELECT
            i.partner_id, '', i.value,
            regexp_replace(upper(i.value), '[[:space:]-]+', '', 'g'),
            '', i.is_primary, 'active', i.source,
            CASE WHEN i.source = 'manual' THEN true ELSE false END
        FROM business_partner_identifiers i
        WHERE i.kind = 'bank_account' AND COALESCE(i.value, '') <> ''
        ON CONFLICT (partner_id, normalized_account_no) DO UPDATE
        SET is_primary = business_partner_bank_accounts.is_primary OR EXCLUDED.is_primary
        """
    )

    # Legacy Supplier becomes a profile of the canonical partner, not a parallel identity root.
    op.execute(
        """
        UPDATE suppliers s
        SET partner_id = bp.id
        FROM business_partners bp
        WHERE bp.legacy_supplier_id = s.id
          AND s.partner_id IS NULL
        """
    )

    # V2 只保留 legacy_supplier_id 的历史值，不再让 canonical 主档反向依赖
    # Supplier 兼容表。否则 business_partners <-> suppliers 会形成外键环。
    op.drop_constraint(
        "fk_business_partners_legacy_supplier",
        "business_partners",
        type_="foreignkey",
    )

    # Materialize all existing confirmed/linked source relationships into direct FKs.
    mappings = [
        ("external_purchase_orders", "supplier_partner_id", "external_purchase_order", "supplier"),
        ("alibaba1688_orders", "supplier_partner_id", "alibaba1688_order", "supplier"),
        ("jackyun_purchase_orders", "supplier_partner_id", "jackyun_purchase_order", "supplier"),
        ("jackyun_purchase_settlements", "supplier_partner_id", "jackyun_purchase_settlement", "supplier"),
        ("jackyun_purchase_returns", "supplier_partner_id", "jackyun_purchase_return", "supplier"),
        ("consumable_purchases", "supplier_partner_id", "consumable_purchase", "supplier"),
        ("jackyun_goods_documents", "supplier_partner_id", "inbound_document", "supplier"),
        ("jky_web_stockin_orders", "supplier_partner_id", "jky_web_stockin_order", "supplier"),
        ("bank_transactions", "counterparty_partner_id", "bank_transaction", "counterparty"),
        ("jky_web_sales_orders", "customer_partner_id", "jky_web_sales_order", "customer"),
    ]
    for table, column, source_type, relation_role in mappings:
        op.execute(
            f"""
            UPDATE {table} src
            SET {column} = l.partner_id
            FROM business_partner_links l
            WHERE l.source_type = '{source_type}'
              AND l.relation_role = '{relation_role}'
              AND l.status = 'linked'
              AND l.partner_id IS NOT NULL
              AND l.source_id = src.id
              AND src.{column} IS NULL
            """
        )

    op.execute(
        """
        UPDATE tax_invoices t
        SET seller_partner_id = l.partner_id
        FROM business_partner_links l
        WHERE l.source_type = 'tax_invoice'
          AND l.relation_role = 'seller'
          AND l.status = 'linked'
          AND l.partner_id IS NOT NULL
          AND l.source_id = t.id
          AND t.seller_partner_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE tax_invoices t
        SET buyer_partner_id = l.partner_id
        FROM business_partner_links l
        WHERE l.source_type = 'tax_invoice'
          AND l.relation_role = 'buyer'
          AND l.status = 'linked'
          AND l.partner_id IS NOT NULL
          AND l.source_id = t.id
          AND t.buyer_partner_id IS NULL
        """
    )


def downgrade() -> None:
    # 恢复到 V1 时重新建立 legacy Supplier 反向外键。
    op.create_foreign_key(
        "fk_business_partners_legacy_supplier",
        "business_partners",
        "suppliers",
        ["legacy_supplier_id"],
        ["id"],
        ondelete="SET NULL",
    )

    for table, column, fk_name in [
        ("sales_orders", "customer_partner_id", "fk_sales_orders_customer_partner"),
        ("jky_web_sales_orders", "customer_partner_id", "fk_jky_web_sales_orders_customer_partner"),
        ("bank_transactions", "counterparty_partner_id", "fk_bank_transactions_counterparty_partner"),
        ("tax_invoices", "buyer_partner_id", "fk_tax_invoices_buyer_partner"),
        ("tax_invoices", "seller_partner_id", "fk_tax_invoices_seller_partner"),
        ("jky_web_stockin_orders", "supplier_partner_id", "fk_jky_web_stockin_orders_supplier_partner"),
        ("consumable_purchases", "supplier_partner_id", "fk_consumable_purchases_supplier_partner"),
        ("jackyun_goods_documents", "supplier_partner_id", "fk_jackyun_goods_documents_supplier_partner"),
        ("jackyun_purchase_returns", "supplier_partner_id", "fk_jackyun_purchase_returns_supplier_partner"),
        ("jackyun_purchase_settlements", "supplier_partner_id", "fk_jackyun_purchase_settlements_supplier_partner"),
        ("jackyun_purchase_orders", "supplier_partner_id", "fk_jackyun_purchase_orders_supplier_partner"),
        ("alibaba1688_orders", "supplier_partner_id", "fk_alibaba1688_orders_supplier_partner"),
        ("external_purchase_orders", "supplier_partner_id", "fk_external_purchase_orders_supplier_partner"),
        ("suppliers", "partner_id", "fk_suppliers_partner"),
    ]:
        op.drop_constraint(fk_name, table, type_="foreignkey")
        op.drop_index(f"ix_{table}_{column}", table_name=table)
        op.drop_column(table, column)

    op.drop_index("ix_business_partner_bank_accounts_status", table_name="business_partner_bank_accounts")
    op.drop_index("ix_business_partner_bank_accounts_normalized_account_no", table_name="business_partner_bank_accounts")
    op.drop_index("ix_business_partner_bank_accounts_partner_id", table_name="business_partner_bank_accounts")
    op.drop_table("business_partner_bank_accounts")
    op.drop_index("ix_business_partner_roles_role", table_name="business_partner_roles")
    op.drop_index("ix_business_partner_roles_partner_id", table_name="business_partner_roles")
    op.drop_table("business_partner_roles")
