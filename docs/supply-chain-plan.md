# 供应链中心改造方案（在现有 8000 系统内扩展）

> 核心原则：**端口是整个程序的入口，不是一个功能一个端口。** 本模块是 `/supply-chain` 路由下的页面群，不是新系统。

## 状态：已落地（2026-09-08 合入 master）

本文的目标**已由 GitHub 分支 `feature/supply-chain-center-v1` = `release/v1.6.0` 实现完毕**，
2026-09-08 全量拉取后合入本地 master（提交 `af1309d` + `686c0bb`）。
**不要再按本文从零开发，直接在现有代码上迭代。**

实际落地的路由（与下文原计划有出入，以这里为准）：

| 路由 | 落地情况 |
|---|---|
| `/supply-chain` | 供应链中心首页，补货面板 `replenishment-panel.tsx` 就挂在首页（原计划单独 `/replenishment` 未单独建） |
| `/supply-chain/production` | 生产订单 + `production-panel.tsx` |
| `/supply-chain/material-flow` | 耗材流转（原计划叫 `/materials`；耗材档案走 `/products/inventory-consumables`） |
| `/supply-chain/in-transit` | 生产执行 / 在途 |
| `/supply-chain/receiving` | 到货入库 |
| `/supply-chain/warehouses` | 仓库（**新增**，可配置厂内/B2C 仓） |
| `/supply-chain/purchase` | **未单独建**，复用 `/purchase/workbench` |
| `/supply-chain/suppliers` | **未单独建**，复用 suppliers 表 |
| `/finance/tax-accounting` | 税务做账（来自 `feature/tax-accounting-v1`） |
| `/finance/tax-accounting/categories` | 分类规则自助维护 |

配套后端：`app/api/v1/supply_chain.py`、`supply_chain_material_flow.py`、`supply_chain_finished_flow.py`、
`services/production_service.py`、`production_material_flow_service.py`、`production_finished_flow_service.py`、
`monthly_core.py`、`closing.py`；迁移含 production / configurable_warehouses / material_movements / finished_flow 等。

> 下文保留为当时的现状分析（数据库 81 张表清单等仍有参考价值）。

## 一、目标路由（原始计划）

| 路由 | 页面 | 说明 |
|---|---|---|
| `/supply-chain` | 供应链中心首页 | 全局概览：库存预警 / 在途 / 生产中 / 待入库 |
| `/supply-chain/replenishment` | 补货工作台（默认首页） | 补货建议 → 创建补货计划 |
| `/supply-chain/production` | 生产订单 | 工厂生产单管理 + 耗材预占/消耗 |
| `/supply-chain/purchase` | 采购订单 | 复用现有采购工作台能力 |
| `/supply-chain/materials` | 耗材管理 | 耗材档案 + 库存 + BOM 关联 |
| `/supply-chain/in-transit` | 在途管理 | 生产/采购在途统一视图 |
| `/supply-chain/receiving` | 到货入库 | 到货 → 核对 → 验收 → 入库 |
| `/supply-chain/suppliers` | 供应商 / 工厂管理 | 复用 suppliers 表 |

## 二、核心业务流

```
库存情况 → 补货建议 → 创建补货计划
    → 选择：A 工厂生产 / B 直接采购（不把补货默认当成采购）
    → 准备耗材（生产单只「预占」耗材；发货到工厂才真正出库）
    → 工厂生产 / 采购执行 → 在途 → 到货
    → 核对数量 → 验收 → 确认入库 → 成品库存增加 → 更新生产单/采购单状态
```

## 三、第一步：现有代码分析（2026-09-07 实测核对）

### 3.1 运行方式
- 启动命令：`cd backend && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000`
- 当前端口：**8000**（前后端同端口，FastAPI 托管 `frontend/out/`）
- 前端入口：`frontend/src/app/**/page.tsx`（Next.js App Router，`output: "export"` 静态导出）
- 后端入口：`backend/app/main.py` → `app/api/v1/__init__.py`（25 个路由模块）
- 前端路由：Next 文件路由 + 侧边栏 `frontend/src/components/sidebar.tsx`（`NAV_ITEMS` 数组）
- 采购模块位置：
  - API：`backend/app/api/v1/purchase.py`、`procurement_workbench.py`、`procurement_chain.py`、`procurement_board.py`
  - 服务：`purchase_service.py`、`procurement_workbench_service.py`、`procurement_chain_service.py`、`consumable_purchase_service.py`
- 库存/商品/耗材位置：
  - 模型：`catalog.py`（Product / ProductSku / Warehouse）、`consumable.py`（Consumable / ConsumableTransaction / ConsumableSkuMapping）、`jackyun.py`（jky_web_total_stock 等）、`sales.py`（SalesOrder / SalesOrderItem）
  - 页面：`/products`（货品档案）、`/products/inventory-goods`（库存-正品）、`/products/inventory-consumables`（库存-耗材）
- 1688：`alibaba1688_imports.py` + `alibaba1688_import_service.py` + `alibaba1688_browser_sync_service.py`（每日 03:50 同步）
- 吉客云：`jackyun_files.py`（文件导入）、`jky_orders.py` / `jky_web.py`（MCP 同步）、`sales_outbound.py`
- 数据库：PostgreSQL **81 张表**（清单见附录 A），Alembic 迁移（⚠ 多 head 分叉，新增迁移需先 `alembic heads`）

### 3.2 可直接复用的能力（不要重复造）

| 供应链需求 | 已有能力 | 位置 |
|---|---|---|
| 正品库存 | 吉客云库存台账 | `jky_web_total_stock` / `jky_web_warehouse_stock` |
| 耗材库存（自有/工厂/在途三口径 + 安全库存 min_stock_qty） | Consumable | `consumables` 表 |
| 耗材出入库流水（append-only，含 send_factory / factory_receive / 负数冲销） | record_transaction | `consumable_service.py` |
| 耗材采购/收货（HC/HR 单，收货即入库） | consumable_purchase_service | `consumable_purchases/receipts` |
| 耗材↔SKU 关联（BOM 雏形） | ConsumableSkuMapping(sku_id, consumable_id) | `consumable_sku_mappings` |
| 采购订单全流程 | 采购工作台 | `/purchase/workbench` |
| 销量数据（近30天/日均） | 销售清单导入（JY 单，1431 有效单） | `sales_orders` / `sales_order_items` |
| 供应商 | suppliers 表 | `suppliers` |
| 采购在途 | 采购状态机 shipped/arrived | `external_purchase_orders` |
| 出库参考（数量口径） | 销售出库报表 | `jackyun_goods_documents` |

### 3.3 缺口（需新增，均需 migration，暂无数据来源的标 TODO）

1. **生产订单**：`production_orders` + `production_order_items`（生产单号/商品/数量/工厂/计划起止/状态/已生产/待生产）。
2. **生产单×耗材行**：`production_order_materials`（预计需用/实际耗用/状态：预占→已发工厂→已消耗/已释放）。
3. **BOM 单耗**：`consumable_sku_mappings` 加 `qty_per_unit` 字段（每件成品耗用数；TODO 默认 1，待用户提供 BOM）。
4. **正品安全库存**：`product_skus` 加 `safety_stock_qty`（TODO：数据待用户提供，先预留字段+可编辑入口）。
5. **补货计划**：`replenishment_plans` + items（来源 production / purchase 二选一，状态机：待执行→生产中/采购中→在途→入库→完成）。
6. **耗材预占**：生产单创建时只预占（占用数 = 单耗 × 生产数量，校验可用 ≥ 预占）；发货到工厂才调 `record_transaction(send_factory)` 出库。

## 四、实施顺序

1. ✅ **第一步（本文档）**：现状分析 + 方案，不大规模重构。
2. **导航与路由**：sidebar.tsx 加「供应链中心」group + `/supply-chain/*` 页面骨架（复用现有 UI 组件与卡片/表格样式，高密度单页风格）。
3. **补货工作台**：真实数据聚合（吉客云库存 + 销售清单销量 + 采购/生产在途），建议补货数量/预计缺货日期；创建补货计划（A 生产 / B 采购）。
4. **生产订单 + 耗材预占**：生产单 CRUD + 状态机 + 预占/发工厂/消耗（对接 record_transaction）。
5. **在途 → 到货 → 入库**：统一在途视图；到货记录 → 核对 → 验收 → 入库（正品走吉客云入库链路，耗材走 HC 收货），更新生产/采购单状态。
6. **旧模块合并检查**：对比采购工作台重复功能，逐步收口，不删历史代码。

## 五、页面设计要求

- 保持现有系统 UI 风格（slate/indigo 色系、圆角浅框、高密度表格）。
- 一行能显示的不拆两行；少折叠；重要数据直接展示；表格带筛选搜索；顶部操作区 sticky；状态用小标签不大卡片。

## 附录 A：数据库 81 张表

aftersales_orders, alembic_version, alibaba1688_file_imports, alibaba1688_orders, allocated_expenses, archive_files, audit_logs, bank_accounts, bank_import_batches, bank_receipt_files, bank_transaction_receipt_links, bank_transactions, closing_versions, consumable_purchase_items, consumable_purchases, consumable_receipts, consumable_sku_mappings, consumable_transactions, consumables, cost_snapshots, counterparty_mapping_rules, email_delivery_logs, exceptions, external_purchase_order_raw_items, external_purchase_orders, finance_delivery_files, finance_delivery_packages, inbound_consumable_usages, inbound_links, integration_connections, integration_credentials, inventory_snapshots, jackyun_file_import_records, jackyun_file_imports, jackyun_goods_document_items, jackyun_goods_documents, jackyun_purchase_order_links, jackyun_purchase_orders, jackyun_purchase_returns, jackyun_purchase_settlements, jackyun_shop_order_items, jackyun_shop_orders, jackyun_stock_allocations, jky_web_sales_order_items, jky_web_sales_orders, jky_web_stockin_items, jky_web_stockin_orders, jky_web_total_stock, jky_web_warehouse_stock, monthly_finance_periods, opening_adjustments, opening_balances, procurement_chain_links, product_skus, products, profit_snapshots, purchase_allocation_items, purchase_extra_expenses, purchase_invoice_links, purchase_invoices, raw_api_payloads, receivable_snapshots, reconciliation_matches, roles, sales_channels, sales_order_items, sales_orders, settlement_records, shipments, stores, suppliers, sync_checkpoints, sync_jobs, sync_logs, tax_invoice_import_records, tax_invoice_imports, tax_invoice_links, tax_invoices, user_roles, users, warehouses
