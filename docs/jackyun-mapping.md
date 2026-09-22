# 吉客云字段 mapping（Phase 1 已取得真实字段样本）

> 原则：不猜字段。以下 mapping 依据 `raw_api_payloads` 中的真实首次响应填写。

## 已订阅接口白名单

```text
ass-business.returnchange.fullinfoget   售后全量
erp-goods.pricelist.get                 价格
erp.allocate.get                        调拨
erp.purch.get                           采购单
erp.purchordersett.get                  采购结算
erp.purchreturn.get                     采购退货
erp.stockquantity.get                   库存
erp.storage.goodsdocin.v2               入库单
erp.storage.goodsdocout.v2              出库单
erp.storage.goodslist                   商品档案
erp.warehouse.get                       仓库
oms.trade.fullinfoget                   线上订单全量
omsapi-business.order.get               OMS 订单
wms.order.query-info.page               发货单
```

## 已确认字段映射（2026-09-02）

| 本地表.字段 | 吉客云字段 | 说明 |
|---|---|---|
| `products.jackyun_goods_id` | `goodsNo` | 商品外部稳定编号 |
| `products.goods_code` | `goodsNo` | 与商品主键同源 |
| `products.goods_name` | `goodsName` | 商品名称 |
| `products.category` | `cateName` | 商品分类 |
| `product_skus.jackyun_sku_id` | `skuBarcode` | 当前可用 SKU 稳定编号/条码 |
| `product_skus.sku_name` | `skuName` | SKU 规格名称 |
| `product_skus.unit` | `unitName` | 单位 |
| `warehouses.jackyun_warehouse_id` | `warehouseCode` | 仓库稳定编号 |
| `warehouses.name` | `warehouseName` | 仓库名称 |
| `inventory_snapshots.quantity` | `currentQuantity` | 当前库存数量 |
| `inventory_snapshots.warehouse_id` | `warehouseCode` → 本地仓库 | 以仓库主档关联 |
| `sales_orders.order_no` | `tradeNo` | 吉客云交易号 |
| `sales_orders.platform` | `shopName` | 店铺名称，原样保留 |
| `sales_orders.order_status` | `tradeStatus` | 原状态码，原样保留 |
| `sales_orders.paid_amount` | `payment` | 实付金额 |
| `sales_order_items.sku_code` | `goodsDetail.goodsNo` | 与 SKU/商品主档关联 |
| `sales_order_items.amount` | `goodsDetail.sellTotal` | 明细销售额 |
| `sales_order_items.unit_price` | `goodsDetail.sellPrice` | 明细单价 |
| `aftersales_orders.aftersale_no` | `returnChangeNo` | 售后/退换单号 |
| `aftersales_orders.order_no` | `sourceTradeNo` | 来源订单号 |
| `aftersales_orders.created_at_src` | `gmtCreate` | 源创建时间 |
| `aftersales_orders.refund_amount` | `returnChangeGoodsDetail.shouldReturnFee` | 明细应退金额求和；无值时保持空 |

## 采购、仓储与网店副本（2026-09-02）

以下接口已经写入本地业务表；表中同时保留每条记录的 `raw` 原始 JSON。没有在真实响应中出现的字段保持空值，不用金额或名称推算。

| 本地表.字段 | 吉客云字段 |
|---|---|
| `jackyun_purchase_settlements.settlement_no` | `settNo` |
| `jackyun_purchase_settlements.settlement_date` | `settDate` |
| `jackyun_purchase_settlements.supplier_name` | `vendName` |
| `jackyun_purchase_settlements.total_amount` | `totalAmount` |
| `jackyun_purchase_settlements.settlement_amount` | `settTotalAmount` |
| `jackyun_purchase_settlements.purchase_fee` | `purFee` |
| `jackyun_purchase_settlements.paid` | `paid` |
| `jackyun_purchase_returns.return_no` | `orderNum`（其他候选编号仅在该字段缺失时使用） |
| `jackyun_purchase_returns.purchase_no` | `purchNo` / `purchId`（响应出现时） |
| `jackyun_goods_documents.goodsdoc_no` | `goodsdocNo` |
| `jackyun_goods_documents.document_at` | `inOutDate` |
| `jackyun_goods_documents.warehouse_code` | `warehouseCode` |
| `jackyun_goods_documents.company_name` | `companyName` |
| `jackyun_goods_document_items.goods_no` | `goodsDocDetailList.goodsNo` |
| `jackyun_goods_document_items.sku_barcode` | `goodsDocDetailList.skuBarcode` |
| `jackyun_goods_document_items.quantity` | `goodsDocDetailList.quantity` |
| `jackyun_stock_allocations.allocate_no` | `allocateNo` |
| `jackyun_stock_allocations.out_warehouse_code` | `outWarehouseCode` |
| `jackyun_stock_allocations.in_warehouse_code` | `inWarehouseCode` |
| `jackyun_shop_orders.shop_order_no` | `tradeOnline.tradeNo` / `tradeNo` |
| `jackyun_shop_orders.logistic_no` | `tradeOnline.logisticNo` |
| `jackyun_shop_orders.created_at_src` | `tradeOnline.createTime` |
| `jackyun_shop_orders.paid_at_src` | `tradeOnline.payTime` |
| `jackyun_shop_orders.goods_count` | `tradeOnline.goodsCount` |
| `jackyun_shop_orders.payment` | `tradeOnline.payment` |
| `jackyun_shop_order_items.plat_goods_id` | `tradeOnlineGoodsList.platGoodsId` |
| `jackyun_shop_order_items.goods_barcode` | `tradeOnlineGoodsList.goodsBarcode`（响应缺失时为空） |
| `jackyun_shop_order_items.goods_name` | `tradeOnlineGoodsList.goodsName` |
| `jackyun_shop_order_items.quantity` | `tradeOnlineGoodsList.sellCount` |
| `jackyun_shop_order_items.unit_price` | `tradeOnlineGoodsList.price` |
| `jackyun_shop_order_items.amount` | `tradeOnlineGoodsList.sellTotal` |

### 已知缺失，不做推测

- 当前 `getTradesListInfo` 响应没有回传 `goodsDetail.sellCount`，因此订单明细数量保持 `NULL`。
- 当前 `getTradesListInfo` 响应没有回传精确下单时间，`sales_orders.ordered_at` 保持 `NULL`。
- `getGoodsPriceListInfo` 本次返回空数组，销售价没有覆盖商品主档。
- 本次真实样本中采购单、采购结算、采购退货、出库、调拨、网店订单部分返回空数组；对应任务仍会成功记录“拉取 0 条”，后续有数据时按外部编号幂等写入。
- 所有 MCP 原始响应都保存在 `raw_api_payloads`；后续拿到官方导出表或新增已授权字段后，以批次和外部编号补齐，不覆盖原始证据。

## 实际响应与预期差异记录

（任何真实 API 响应与文档不一致处记录于此，不偷偷改业务定义）

## MCP 工具清单（2026-09-01 tools/list 实测，open-platform-mcp v1.0.0）

| API method | MCP tool | 必填入参 |
|---|---|---|
| erp.storage.goodsdocin.v2 | getGoodsDocInListInfo | pageSize, selelctFields |
| erp.storage.goodsdocout.v2 | getGoodsDocOutListInfo | pageSize, selelctFields |
| erp.storage.goodslist | getGoodsListInfoByGoodsNo | pageSize |
| erp-goods.pricelist.get | getGoodsPriceListInfo | pageSize, cols |
| erp.stockquantity.get | getGoodsStockQuantityListInfo | pageSize |
| omsapi-business.order.get | getOrderListInfo | pageSize |
| erp.purch.get | getPurchOrderListInfo | pageSize |
| erp.purchreturn.get | getPurchOrderReturnListInfo | pageSize |
| erp.purchordersett.get | getPurchOrderSettleListInfo | pageSize, cols |
| ass-business.returnchange.fullinfoget | getReturnChangeListInfo | pageSize |
| wms.order.query-info.page | getShopOrderLiseInfo | pageSize, fields |
| oms.trade.fullinfoget | getTradesListInfo | fields, pageSize |
| erp.allocate.get | getStockAllocateListInfo | pageSize |
| erp.warehouse.get | getWarehouseListInfo | pageSize |

## 认证（实测确认）

- `Authorization: <裸Token>`，**不带 Bearer 前缀**（Bearer 形式返回 401 Invalid token）
- 会话经 `Mcp-Session-Id` 响应头保持
- 传输：POST JSON-RPC 2.0 到 `https://mcp.open.jackyun.com/mcp/messages`，Accept: `application/json, text/event-stream`

## 首次 tools/call 真实响应（2026-09-01）

三个只读接口均返回：

```json
{"code":0,"msg":"该应用未开通开放平台，无法调用接口，请联系客户经理处理","result":{"data":null},"subCode":"0130000609"}
```

**结论**：MCP 通道正常，业务侧需联系吉客云客户经理开通开放平台 API 权限。开通前字段 mapping 保持待填。
