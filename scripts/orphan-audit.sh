#!/usr/bin/env bash
# 老业务表 ForeignKey / 多态引用补强前的只读孤儿数据审计。
# 不修改任何数据；发现孤儿记录时以非 0 退出，禁止直接加 FK 或假设链路完整。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$ROOT/.env" ]]; then
  set -a
  source "$ROOT/.env"
  set +a
fi

if ! command -v psql >/dev/null 2>&1; then
  echo "缺少 psql，无法执行孤儿数据审计。"
  exit 2
fi

export PGPASSWORD="${POSTGRES_PASSWORD:-}"
PSQL=(psql -X -v ON_ERROR_STOP=1 \
  -h "${POSTGRES_HOST:-localhost}" \
  -p "${POSTGRES_PORT:-5432}" \
  -U "${POSTGRES_USER:-ecommerce}" \
  -d "${POSTGRES_DB:-ecommerce}")

SQL="
WITH checks AS (
  SELECT 'consumable_sku_mappings.sku_id -> product_skus.id' AS relation, m.id
  FROM consumable_sku_mappings m
  LEFT JOIN product_skus p ON p.id = m.sku_id
  WHERE p.id IS NULL

  UNION ALL
  SELECT 'consumable_sku_mappings.consumable_id -> consumables.id', m.id
  FROM consumable_sku_mappings m
  LEFT JOIN consumables c ON c.id = m.consumable_id
  WHERE c.id IS NULL

  UNION ALL
  SELECT 'consumable_transactions.consumable_id -> consumables.id', t.id
  FROM consumable_transactions t
  LEFT JOIN consumables c ON c.id = t.consumable_id
  WHERE c.id IS NULL

  UNION ALL
  SELECT 'inbound_consumable_usages.consumable_id -> consumables.id', u.id
  FROM inbound_consumable_usages u
  LEFT JOIN consumables c ON c.id = u.consumable_id
  WHERE c.id IS NULL

  UNION ALL
  SELECT 'inbound_consumable_usages.link_id -> procurement_chain_links.id', u.id
  FROM inbound_consumable_usages u
  LEFT JOIN procurement_chain_links l ON l.id = u.link_id
  WHERE l.id IS NULL

  UNION ALL
  SELECT 'inbound_consumable_usages.inbound_document_id -> jackyun_goods_documents.id', u.id
  FROM inbound_consumable_usages u
  LEFT JOIN jackyun_goods_documents d ON d.id = u.inbound_document_id
  WHERE d.id IS NULL

  UNION ALL
  SELECT 'sales_order_items.order_id -> sales_orders.id', i.id
  FROM sales_order_items i
  LEFT JOIN sales_orders o ON o.id = i.order_id
  WHERE o.id IS NULL

  UNION ALL
  SELECT 'sales_order_items.sku_id -> product_skus.id', i.id
  FROM sales_order_items i
  LEFT JOIN product_skus p ON p.id = i.sku_id
  WHERE i.sku_id IS NOT NULL AND p.id IS NULL

  UNION ALL
  SELECT 'shipments.order_id -> sales_orders.id', s.id
  FROM shipments s
  LEFT JOIN sales_orders o ON o.id = s.order_id
  WHERE o.id IS NULL

  UNION ALL
  SELECT 'jackyun_goods_document_items.document_id -> jackyun_goods_documents.id', i.id
  FROM jackyun_goods_document_items i
  LEFT JOIN jackyun_goods_documents d ON d.id = i.document_id
  WHERE d.id IS NULL

  UNION ALL
  SELECT 'jackyun_goods_document_items.matched_sku_id -> product_skus.id', i.id
  FROM jackyun_goods_document_items i
  LEFT JOIN product_skus p ON p.id = i.matched_sku_id
  WHERE i.matched_sku_id IS NOT NULL AND p.id IS NULL

  UNION ALL
  SELECT 'procurement_chain_links.target_id(inbound) -> jackyun_goods_documents.id', l.id
  FROM procurement_chain_links l
  LEFT JOIN jackyun_goods_documents d ON d.id = l.target_id
  WHERE l.target_type = 'inbound' AND d.id IS NULL

  UNION ALL
  SELECT 'procurement_chain_links.target_id(settlement) -> jackyun_purchase_settlements.id', l.id
  FROM procurement_chain_links l
  LEFT JOIN jackyun_purchase_settlements s ON s.id = l.target_id
  WHERE l.target_type = 'settlement' AND s.id IS NULL

  UNION ALL
  SELECT 'external_purchase_order_raw_items.po_id -> external_purchase_orders.id', i.id
  FROM external_purchase_order_raw_items i
  LEFT JOIN external_purchase_orders p ON p.id = i.po_id
  WHERE p.id IS NULL

  UNION ALL
  SELECT 'purchase_allocation_items.po_id -> external_purchase_orders.id', i.id
  FROM purchase_allocation_items i
  LEFT JOIN external_purchase_orders p ON p.id = i.po_id
  WHERE p.id IS NULL

  UNION ALL
  SELECT 'purchase_allocation_items.sku_id -> product_skus.id', i.id
  FROM purchase_allocation_items i
  LEFT JOIN product_skus p ON p.id = i.sku_id
  WHERE i.sku_id IS NOT NULL AND p.id IS NULL

  UNION ALL
  SELECT 'purchase_allocation_items.source_item_id -> jackyun_goods_document_items.id', i.id
  FROM purchase_allocation_items i
  LEFT JOIN jackyun_goods_document_items d ON d.id = i.source_item_id
  WHERE i.source_item_id IS NOT NULL AND d.id IS NULL

  UNION ALL
  SELECT 'purchase_extra_expenses.po_id -> external_purchase_orders.id', e.id
  FROM purchase_extra_expenses e
  LEFT JOIN external_purchase_orders p ON p.id = e.po_id
  WHERE p.id IS NULL

  UNION ALL
  SELECT 'purchase_invoice_links.invoice_id -> purchase_invoices.id', l.id
  FROM purchase_invoice_links l
  LEFT JOIN purchase_invoices i ON i.id = l.invoice_id
  WHERE i.id IS NULL

  UNION ALL
  SELECT 'purchase_invoice_links.po_id -> external_purchase_orders.id', l.id
  FROM purchase_invoice_links l
  LEFT JOIN external_purchase_orders p ON p.id = l.po_id
  WHERE p.id IS NULL

  UNION ALL
  SELECT 'tax_invoice_import_records.import_id -> tax_invoice_imports.id', r.id
  FROM tax_invoice_import_records r
  LEFT JOIN tax_invoice_imports i ON i.id = r.import_id
  WHERE i.id IS NULL

  UNION ALL
  SELECT 'tax_invoice_import_records.invoice_id -> tax_invoices.id', r.id
  FROM tax_invoice_import_records r
  LEFT JOIN tax_invoices i ON i.id = r.invoice_id
  WHERE r.invoice_id IS NOT NULL AND i.id IS NULL

  UNION ALL
  SELECT 'tax_invoice_links.invoice_id -> tax_invoices.id', l.id
  FROM tax_invoice_links l
  LEFT JOIN tax_invoices i ON i.id = l.invoice_id
  WHERE i.id IS NULL

), ranked AS (
  SELECT relation, id, row_number() OVER (PARTITION BY relation ORDER BY id) AS rn
  FROM checks
), names(relation) AS (
  VALUES
    ('consumable_sku_mappings.sku_id -> product_skus.id'),
    ('consumable_sku_mappings.consumable_id -> consumables.id'),
    ('consumable_transactions.consumable_id -> consumables.id'),
    ('inbound_consumable_usages.consumable_id -> consumables.id'),
    ('inbound_consumable_usages.link_id -> procurement_chain_links.id'),
    ('inbound_consumable_usages.inbound_document_id -> jackyun_goods_documents.id'),
    ('sales_order_items.order_id -> sales_orders.id'),
    ('sales_order_items.sku_id -> product_skus.id'),
    ('shipments.order_id -> sales_orders.id'),
    ('jackyun_goods_document_items.document_id -> jackyun_goods_documents.id'),
    ('jackyun_goods_document_items.matched_sku_id -> product_skus.id'),
    ('procurement_chain_links.target_id(inbound) -> jackyun_goods_documents.id'),
    ('procurement_chain_links.target_id(settlement) -> jackyun_purchase_settlements.id'),
    ('external_purchase_order_raw_items.po_id -> external_purchase_orders.id'),
    ('purchase_allocation_items.po_id -> external_purchase_orders.id'),
    ('purchase_allocation_items.sku_id -> product_skus.id'),
    ('purchase_allocation_items.source_item_id -> jackyun_goods_document_items.id'),
    ('purchase_extra_expenses.po_id -> external_purchase_orders.id'),
    ('purchase_invoice_links.invoice_id -> purchase_invoices.id'),
    ('purchase_invoice_links.po_id -> external_purchase_orders.id'),
    ('tax_invoice_import_records.import_id -> tax_invoice_imports.id'),
    ('tax_invoice_import_records.invoice_id -> tax_invoices.id'),
    ('tax_invoice_links.invoice_id -> tax_invoices.id')
)
SELECT n.relation,
       count(r.id) AS orphan_count,
       coalesce(string_agg(r.id::text, ',' ORDER BY r.id) FILTER (WHERE r.rn <= 10), '') AS sample_ids
FROM names n
LEFT JOIN ranked r ON r.relation = n.relation
GROUP BY n.relation
ORDER BY n.relation;
"

echo "==> Referential-integrity orphan audit（只读）"
"${PSQL[@]}" -P pager=off -c "$SQL"

TOTAL_SQL="
SELECT
  (SELECT count(*) FROM consumable_sku_mappings m LEFT JOIN product_skus p ON p.id=m.sku_id WHERE p.id IS NULL) +
  (SELECT count(*) FROM consumable_sku_mappings m LEFT JOIN consumables c ON c.id=m.consumable_id WHERE c.id IS NULL) +
  (SELECT count(*) FROM consumable_transactions t LEFT JOIN consumables c ON c.id=t.consumable_id WHERE c.id IS NULL) +
  (SELECT count(*) FROM inbound_consumable_usages u LEFT JOIN consumables c ON c.id=u.consumable_id WHERE c.id IS NULL) +
  (SELECT count(*) FROM inbound_consumable_usages u LEFT JOIN procurement_chain_links l ON l.id=u.link_id WHERE l.id IS NULL) +
  (SELECT count(*) FROM inbound_consumable_usages u LEFT JOIN jackyun_goods_documents d ON d.id=u.inbound_document_id WHERE d.id IS NULL) +
  (SELECT count(*) FROM sales_order_items i LEFT JOIN sales_orders o ON o.id=i.order_id WHERE o.id IS NULL) +
  (SELECT count(*) FROM sales_order_items i LEFT JOIN product_skus p ON p.id=i.sku_id WHERE i.sku_id IS NOT NULL AND p.id IS NULL) +
  (SELECT count(*) FROM shipments s LEFT JOIN sales_orders o ON o.id=s.order_id WHERE o.id IS NULL) +
  (SELECT count(*) FROM jackyun_goods_document_items i LEFT JOIN jackyun_goods_documents d ON d.id=i.document_id WHERE d.id IS NULL) +
  (SELECT count(*) FROM jackyun_goods_document_items i LEFT JOIN product_skus p ON p.id=i.matched_sku_id WHERE i.matched_sku_id IS NOT NULL AND p.id IS NULL) +
  (SELECT count(*) FROM procurement_chain_links l LEFT JOIN jackyun_goods_documents d ON d.id=l.target_id WHERE l.target_type='inbound' AND d.id IS NULL) +
  (SELECT count(*) FROM procurement_chain_links l LEFT JOIN jackyun_purchase_settlements s ON s.id=l.target_id WHERE l.target_type='settlement' AND s.id IS NULL) +
  (SELECT count(*) FROM external_purchase_order_raw_items i LEFT JOIN external_purchase_orders p ON p.id=i.po_id WHERE p.id IS NULL) +
  (SELECT count(*) FROM purchase_allocation_items i LEFT JOIN external_purchase_orders p ON p.id=i.po_id WHERE p.id IS NULL) +
  (SELECT count(*) FROM purchase_allocation_items i LEFT JOIN product_skus p ON p.id=i.sku_id WHERE i.sku_id IS NOT NULL AND p.id IS NULL) +
  (SELECT count(*) FROM purchase_allocation_items i LEFT JOIN jackyun_goods_document_items d ON d.id=i.source_item_id WHERE i.source_item_id IS NOT NULL AND d.id IS NULL) +
  (SELECT count(*) FROM purchase_extra_expenses e LEFT JOIN external_purchase_orders p ON p.id=e.po_id WHERE p.id IS NULL) +
  (SELECT count(*) FROM purchase_invoice_links l LEFT JOIN purchase_invoices i ON i.id=l.invoice_id WHERE i.id IS NULL) +
  (SELECT count(*) FROM purchase_invoice_links l LEFT JOIN external_purchase_orders p ON p.id=l.po_id WHERE p.id IS NULL) +
  (SELECT count(*) FROM tax_invoice_import_records r LEFT JOIN tax_invoice_imports i ON i.id=r.import_id WHERE i.id IS NULL) +
  (SELECT count(*) FROM tax_invoice_import_records r LEFT JOIN tax_invoices i ON i.id=r.invoice_id WHERE r.invoice_id IS NOT NULL AND i.id IS NULL) +
  (SELECT count(*) FROM tax_invoice_links l LEFT JOIN tax_invoices i ON i.id=l.invoice_id WHERE i.id IS NULL);
"
TOTAL="$("${PSQL[@]}" -Atc "$TOTAL_SQL")"

if [[ "$TOTAL" != "0" ]]; then
  echo "==> 发现 $TOTAL 条孤儿引用：暂时禁止新增对应 ForeignKey/约束。先核对并修复真实数据。"
  exit 1
fi

echo "==> 通过：当前审计范围未发现孤儿引用，可进入约束 migration 设计阶段。"
