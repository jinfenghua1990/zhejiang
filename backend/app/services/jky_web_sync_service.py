"""吉客云 Web Adapter 同步编排服务。

流程（参考项目吉客云_每日数据总同步 的编排思路，按用户规格 12 收敛为单任务）：
  Session Check → 销售订单 → 销售明细 → 采购入库(主+明细) → 总库存 → 分仓库存
  → 数据质量检查（行数校验）→ 状态更新

稳定性原则：
- 单模块失败不影响其他模块（模块级隔离）；
- 登录失效/需验证 → 终止后续步骤并明确置位连接状态；
- 库存全量快照：单事务 DELETE+INSERT，失败回滚不清空旧库存；
- 所有 upsert 幂等（重复同步 = 更新而非新增）；
- Decimal/datetime 写 JSONB 前统一转字符串（项目既有教训）。
"""

from __future__ import annotations

import io
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.adapters.jackyun import finish_sync_job, start_sync_job
from app.adapters.jky_web import (
    JkyWebClient,
    JkyWebError,
    JkyWebSessionManager,
    PROVIDER,
    SESSION_ACTIVE,
    SESSION_ERROR,
    SESSION_NEED_LOGIN,
    SESSION_NEED_VERIFY,
    merge_curl_texts,
)
from app.config import settings
from app.models.integration import SyncJob, SyncLog
from app.models.jky_web import (
    JkyWebSalesOrder,
    JkyWebSalesOrderItem,
    JkyWebStockinItem,
    JkyWebStockinOrder,
    JkyWebTotalStock,
    JkyWebWarehouseStock,
)

# ---------------------------------------------------------------------------
# 基础规范化
# ---------------------------------------------------------------------------

def _jsonb_safe(value: Any) -> Any:
    """Decimal/datetime → 字符串，防止 JSONB 序列化崩溃（项目既有教训）。"""
    if isinstance(value, dict):
        return {str(k): _jsonb_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonb_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def as_datetime(value: Any) -> datetime | None:
    """兼容毫秒/秒时间戳与 ISO 字符串（参考项目 as_datetime）。"""
    if value in (None, "", 0, "0"):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            number = float(value)
            return datetime.fromtimestamp(
                number / 1000 if number > 10_000_000_000 else number
            )
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, OSError, OverflowError):
        return None


def as_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


# ---------------------------------------------------------------------------
# Upsert / 快照
# ---------------------------------------------------------------------------

def _chunks(rows: list[dict[str, Any]], size: int):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def _upsert_rows(
    db: Session, model, rows: list[dict[str, Any]], conflict_cols: list[str],
    update_cols: list[str], batch_size: int = 500,
) -> dict[str, int]:
    """PostgreSQL 幂等 upsert；用 xmax==0 判定新插入 vs 更新。"""
    inserted = updated = 0
    for batch in _chunks(rows, batch_size):
        if not batch:
            continue
        stmt = pg_insert(model).values(batch)
        clause = {c: getattr(stmt.excluded, c) for c in update_cols}
        stmt = stmt.on_conflict_do_update(index_elements=conflict_cols, set_=clause)
        # xmax = 0 表示新插入行，否则是更新行（PostgreSQL 惯用技巧）
        stmt = stmt.returning(text("(xmax = 0) AS _inserted"))
        for (flag,) in db.execute(stmt).fetchall():
            if flag:
                inserted += 1
            else:
                updated += 1
    return {"inserted": inserted, "updated": updated}


def _snapshot_replace(
    db: Session, model, rows: list[dict[str, Any]], label: str,
) -> dict[str, int]:
    """全量快照：单事务内先删后插；异常回滚保住旧库存。"""
    if not rows:
        raise JkyWebError(f"{label} 拉取结果为空：疑似登录态或接口异常，为避免清空库存已中止", "api")
    inserted = 0
    db.query(model).delete(synchronize_session=False)
    for batch in _chunks(rows, 500):
        db.execute(pg_insert(model).values(batch))
        inserted += len(batch)
    return {"inserted": inserted, "updated": 0}


# ---------------------------------------------------------------------------
# 销售数据（网页导出任务流程：xlsx）
# ---------------------------------------------------------------------------

SALES_ORDER_EN = [
    "flagIds", "tradeNo", "tradeStatusExplain", "settleStatusExplain", "shopName",
    "handleTime", "payTime", "warehouseName", "logisticName", "mainPostid",
    "sourceTradeNo", "consignTime", "tradeTypeExplain", "payment", "tradeCount",
    "goodslist", "mergeRemarks", "tradeTime", "shopCateName", "realFee", "city",
    "platWarehouseCode", "customerCode", "customerAccount", "customerName",
]
SALES_ORDER_CN = [
    "标记", "订单编号", "订单状态", "结算状态", "销售渠道", "处理时间", "付款时间",
    "发货仓库", "物流公司", "物流单号", "网店订单号", "发货时间", "订单类型",
    "应收合计", "货品数量", "货品摘要", "合并备注", "下单时间", "渠道分类",
    "实付金额", "市", "仓库编码（平台）", "客户编号", "客户账号", "客户名称",
]
SALES_ITEM_EN = [
    "tradeNo", "sourceTradeNo", "shopName", "shopCateName", "goodsNo", "goodsName",
    "barcode", "logisticName", "warehouseName", "brandName", "specName", "sellCount",
    "sellPrice", "discountFee", "discountRate", "sellTotal", "cost", "afterShareUnitFee",
    "shareFavourableFee", "afterShareFee", "otherShareFavourableFee", "grossProfit",
    "grossProfitRate", "price1", "price6", "price7", "tradeTime", "payTime",
    "cateName", "city", "customerCode",
]
SALES_ITEM_CN = [
    "订单编号", "网店订单号", "销售渠道", "渠道分类", "货品编号", "货品名称",
    "货品条码", "物流公司", "发货仓库", "品牌", "规格", "数量", "单价", "优惠",
    "折扣", "金额", "货品成本", "分摊后单价", "分摊金额", "分摊后金额", "费用分摊",
    "毛利", "毛利率", "价格-零售价", "价格-含税价", "价格-不含税价", "下单时间",
    "付款时间", "货品分类", "市", "客户编号",
]


def _parse_xlsx(content: bytes, cn_headers: list[str]) -> list[dict[str, Any]]:
    """解析导出 xlsx → [{中文名: 值}]；忽略空行与序号列。"""
    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        sheet = workbook.active
        rows_iter = sheet.iter_rows(values_only=True)
        header_row = next(rows_iter, None)
        if not header_row:
            return []
        headers = [
            str(c).strip().replace("\n", "") if c is not None else ""
            for c in header_row
        ]
        wanted = [h for h in cn_headers]
        out: list[dict[str, Any]] = []
        for values in rows_iter:
            if values is None or all(v is None or str(v).strip() == "" for v in values):
                continue
            row = dict(zip(headers, values))
            # 序号/行号列为噪声，不属于业务列
            row.pop("序号", None)
            row.pop("行号", None)
            if not any(row.get(h) not in (None, "") for h in wanted):
                continue
            out.append(row)
        return out
    finally:
        workbook.close()


def _normalize_sales_order(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "trade_no": _text(row.get("订单编号")),
        "trade_status": _text(row.get("订单状态")),
        "settle_status": _text(row.get("结算状态")),
        "shop_name": _text(row.get("销售渠道")),
        "shop_cate_name": _text(row.get("渠道分类")),
        "source_trade_no": _text(row.get("网店订单号")),
        "trade_type": _text(row.get("订单类型")),
        "trade_time": as_datetime(row.get("下单时间")),
        "pay_time": as_datetime(row.get("付款时间")),
        "handle_time": as_datetime(row.get("处理时间")),
        "consign_time": as_datetime(row.get("发货时间")),
        "warehouse_name": _text(row.get("发货仓库")),
        "plat_warehouse_code": _text(row.get("仓库编码（平台）")),
        "logistic_name": _text(row.get("物流公司")),
        "logistic_no": _text(row.get("物流单号")),
        "trade_count": as_decimal(row.get("货品数量")),
        "goods_summary": _text(row.get("货品摘要")),
        "merge_remarks": _text(row.get("合并备注")),
        "payment": as_decimal(row.get("应收合计")),
        "real_fee": as_decimal(row.get("实付金额")),
        "customer_code": _text(row.get("客户编号")),
        "customer_account": _text(row.get("客户账号")),
        "city": _text(row.get("市")),
        "raw": _jsonb_safe(row),
    }


def _normalize_sales_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "trade_no": _text(row.get("订单编号")),
        "source_trade_no": _text(row.get("网店订单号")),
        "shop_name": _text(row.get("销售渠道")),
        "shop_cate_name": _text(row.get("渠道分类")),
        "goods_no": _text(row.get("货品编号")),
        "goods_name": _text(row.get("货品名称")),
        "barcode": _text(row.get("货品条码")),
        "spec_name": _text(row.get("规格")),
        "brand_name": _text(row.get("品牌")),
        "cate_name": _text(row.get("货品分类")),
        "warehouse_name": _text(row.get("发货仓库")),
        "logistic_name": _text(row.get("物流公司")),
        "sell_count": as_decimal(row.get("数量")),
        "sell_price": as_decimal(row.get("单价")),
        "discount_fee": as_decimal(row.get("优惠")),
        "discount_rate": as_decimal(row.get("折扣")),
        "sell_total": as_decimal(row.get("金额")),
        "cost": as_decimal(row.get("货品成本")),
        "after_share_unit_fee": as_decimal(row.get("分摊后单价")),
        "share_favourable_fee": as_decimal(row.get("分摊金额")),
        "after_share_fee": as_decimal(row.get("分摊后金额")),
        "other_share_fee": as_decimal(row.get("费用分摊")),
        "gross_profit": as_decimal(row.get("毛利")),
        "gross_profit_rate": as_decimal(row.get("毛利率")),
        "price1": as_decimal(row.get("价格-零售价")),
        "price6": as_decimal(row.get("价格-含税价")),
        "price7": as_decimal(row.get("价格-不含税价")),
        "trade_time": as_datetime(row.get("下单时间")),
        "pay_time": as_datetime(row.get("付款时间")),
        "customer_code": _text(row.get("客户编号")),
        "raw": _jsonb_safe(row),
    }


def _export_and_parse(
    client: JkyWebClient, excel_type: str, type_name: str,
    en_names: list[str], cn_names: list[str], filter_key: str,
    start: datetime, end: datetime,
) -> list[dict[str, Any]]:
    params = client._export_params(excel_type, type_name, en_names, cn_names, filter_key, start, end)
    task_id = client.start_export_task(params)
    download_url, _attachment = client.poll_export_task(task_id)
    content = client.download_export(download_url)
    rows = _parse_xlsx(content, cn_names)
    if len(rows) >= settings.JKY_WEB_EXPORT_SPLIT_ROWS:
        raise JkyWebError(
            f"{type_name} 单窗口行数 {len(rows)} 达到拆分阈值，请缩小同步窗口或联系管理员分批执行", "api"
        )
    return rows


def sync_sales_orders(db: Session, client: JkyWebClient, start: datetime, end: datetime) -> dict[str, int]:
    rows = _export_and_parse(
        client, "2", "销售单查询", SALES_ORDER_EN, SALES_ORDER_CN, "jsonStr", start, end
    )
    records = [_normalize_sales_order(r) for r in rows if _text(r.get("订单编号"))]
    stats = _upsert_rows(
        db, JkyWebSalesOrder, records, ["trade_no"],
        [
            "trade_status", "settle_status", "shop_name", "shop_cate_name", "source_trade_no",
            "trade_type", "trade_time", "pay_time", "handle_time", "consign_time",
            "warehouse_name", "plat_warehouse_code", "logistic_name", "logistic_no",
            "trade_count", "goods_summary", "merge_remarks", "payment", "real_fee",
            "customer_code", "customer_account", "city", "raw", "updated_at",
        ],
    )
    db.commit()
    return stats


def sync_sales_details(db: Session, client: JkyWebClient, start: datetime, end: datetime) -> dict[str, int]:
    rows = _export_and_parse(
        client, "11", "销售单明细账", SALES_ITEM_EN, SALES_ITEM_CN, "filterOrderDetailDto", start, end
    )
    records = [_normalize_sales_item(r) for r in rows if _text(r.get("订单编号"))]
    # 明细无稳定外部唯一键：按下单时间窗口整体替换（参考项目同款模式，天然幂等）
    db.query(JkyWebSalesOrderItem).filter(
        JkyWebSalesOrderItem.trade_time >= start,
        JkyWebSalesOrderItem.trade_time <= end,
    ).delete(synchronize_session=False)
    inserted = 0
    for batch in _chunks(records, 500):
        db.execute(pg_insert(JkyWebSalesOrderItem).values(batch))
        inserted += len(batch)
    db.commit()
    return {"inserted": inserted, "updated": 0, "replaced_window": f"{start.isoformat()} ~ {end.isoformat()}"}


# ---------------------------------------------------------------------------
# 采购入库（直连接口分页）
# ---------------------------------------------------------------------------

def _normalize_stockin_order(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "doc_id": _text(row.get("docId")),
        "goodsdoc_no": _text(row.get("goodsdocNo")),
        "out_bill_no": _text(row.get("outBillNo")),
        "in_out_date": as_datetime(row.get("inOutDate")),
        "inout_type": _text(row.get("inouttype")),
        "inout_type_name": _text(row.get("inouttypeName")),
        "warehouse_id": _text(row.get("warehouseId")),
        "warehouse_name": _text(row.get("warehouseName")),
        "bill_no": _text(row.get("billNo")),
        "source_bill_no": _text(row.get("sourceBillNo")),
        "supplier_name": _text(row.get("vendCustomerName")),
        "logistic_name": _text(row.get("logisticName")),
        "logistic_no": _text(row.get("logisticNo")),
        "company_name": _text(row.get("companyName")),
        "total_quantity": as_decimal(row.get("totalQuantity")),
        "cost_total_amount": as_decimal(row.get("baseCostTotalAmount")),
        "has_tax_total_amount": as_decimal(row.get("baseHasTaxTotalAmount")),
        "tax_total_amount": as_decimal(row.get("baseTaxTotalAmount")),
        "red_status": _text(row.get("redStatus")),
        "remark": _text(row.get("goodsdocRemark")),
        "raw": _jsonb_safe(row),
    }


def _normalize_stockin_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "rec_id": _text(row.get("recId")),
        "doc_id": _text(row.get("headId") or row.get("docId")),
        "goodsdoc_no": _text(row.get("goodsdocNo")),
        "order_no": _text(row.get("orderNum")),
        "goods_id": _text(row.get("goodsId")),
        "goods_no": _text(row.get("goodsNo")),
        "goods_name": _text(row.get("goodsName")),
        "sku_id": _text(row.get("skuId")),
        "spec_name": _text(row.get("skuName")),
        "barcode": _text(row.get("skuBarcode")),
        "brand_name": _text(row.get("brandName")),
        "cate_name": _text(row.get("cateName")),
        "quantity": as_decimal(row.get("quantity")),
        "unit_name": _text(row.get("unitName")),
        "cost_price": as_decimal(row.get("baceCurrencyCostPrice")),
        "cost_amount": as_decimal(row.get("baceCurrencyCostAmount")),
        "with_tax_price": as_decimal(row.get("baceCurrencyWithTaxPrice")),
        "with_tax_amount": as_decimal(row.get("baceCurrencyWithTaxAmount")),
        "tax_rate": as_decimal(row.get("taxRate")),
        "batch_no": _text(row.get("batchNo")),
        "production_date": as_datetime(row.get("productionDate")),
        "expiration_date": as_datetime(row.get("expirationDate")),
        "shelf_life": _text(row.get("shelfLife")),
        "warehouse_id": _text(row.get("warehouseId")),
        "raw": _jsonb_safe(row),
    }


def sync_stockin(db: Session, client: JkyWebClient, start: datetime, end: datetime) -> dict[str, Any]:
    headers = client.fetch_stockin_headers(start, end)
    order_records = [_normalize_stockin_order(r) for r in headers if _text(r.get("docId"))]
    stats = _upsert_rows(
        db, JkyWebStockinOrder, order_records, ["doc_id"],
        [
            "goodsdoc_no", "out_bill_no", "in_out_date", "inout_type", "inout_type_name",
            "warehouse_id", "warehouse_name", "bill_no", "source_bill_no", "supplier_name",
            "logistic_name", "logistic_no", "company_name", "total_quantity",
            "cost_total_amount", "has_tax_total_amount", "tax_total_amount", "red_status",
            "remark", "raw", "updated_at",
        ],
    )
    item_stats = {"inserted": 0, "updated": 0}
    doc_ids = [r["doc_id"] for r in order_records]
    if doc_ids:
        details = client.fetch_stockin_details(doc_ids)
        item_records = [_normalize_stockin_item(r) for r in details if _text(r.get("recId"))]
        item_stats = _upsert_rows(
            db, JkyWebStockinItem, item_records, ["rec_id"],
            [
                "doc_id", "goodsdoc_no", "order_no", "goods_id", "goods_no", "goods_name",
                "sku_id", "spec_name", "barcode", "brand_name", "cate_name", "quantity",
                "unit_name", "cost_price", "cost_amount", "with_tax_price", "with_tax_amount",
                "tax_rate", "batch_no", "production_date", "expiration_date", "shelf_life",
                "warehouse_id", "raw", "updated_at",
            ],
        )
    db.commit()
    return {"orders": stats, "items": item_stats, "doc_ids": len(doc_ids)}


# ---------------------------------------------------------------------------
# 库存（全量快照）
# ---------------------------------------------------------------------------

def _sku_key(row: dict[str, Any]) -> str:
    sku_id = _text(row.get("skuId"))
    if sku_id:
        return sku_id
    return f"{_text(row.get('goodsNo'))}|{_text(row.get('skuName'))}"


def _warehouse_key(row: dict[str, Any]) -> str:
    warehouse_id = _text(row.get("warehouseId"))
    if warehouse_id:
        return warehouse_id
    return _text(row.get("warehouseName"))


def _normalize_total_stock(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "sku_key": _sku_key(row),
        "sku_id": _text(row.get("skuId")),
        "goods_id": _text(row.get("goodsId")),
        "goods_no": _text(row.get("goodsNo")),
        "goods_name": _text(row.get("goodsName")),
        "spec_name": _text(row.get("skuName")),
        "barcode": _text(row.get("skuBarcode")),
        "brand_name": _text(row.get("brandName")),
        "cate_name": _text(row.get("cateName")),
        "unit_name": _text(row.get("unitName")),
        "warehouse_id": _text(row.get("warehouseId")),
        "warehouse_name": _text(row.get("warehouseName")),
        "current_quantity": as_decimal(row.get("currentQuantity")),
        "locking_quantity": as_decimal(row.get("lockingQuantity")),
        "can_use_quantity": as_decimal(row.get("canUseQuantity")),
        "order_able_quantity": as_decimal(row.get("orderAbleQuantity")),
        "yesterday_quantity": as_decimal(row.get("yesterdayQuantity")),
        "week_quantity": as_decimal(row.get("weekQuantity")),
        "threeday_quantity": as_decimal(row.get("threedayQuantity")),
        "total_sale_quantity": as_decimal(row.get("totalSaleQuantity")),
        "price1": as_decimal(row.get("price1")),
        "price6": as_decimal(row.get("price6")),
        "price7": as_decimal(row.get("price7")),
        "raw": _jsonb_safe(row),
    }


def _normalize_warehouse_stock(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "warehouse_key": _warehouse_key(row),
        "warehouse_id": _text(row.get("warehouseId")),
        "warehouse_name": _text(row.get("warehouseName")),
        "sku_key": _sku_key(row),
        "sku_id": _text(row.get("skuId")),
        "goods_id": _text(row.get("goodsId")),
        "goods_no": _text(row.get("goodsNo")),
        "goods_name": _text(row.get("goodsName")),
        "spec_name": _text(row.get("skuName")),
        "barcode": _text(row.get("skuBarcode")),
        "brand_name": _text(row.get("brandName")),
        "cate_name": _text(row.get("cateName")),
        "current_quantity": as_decimal(row.get("currentQuantity")),
        "can_use_quantity": as_decimal(row.get("canUseQuantity")),
        "locking_quantity": as_decimal(row.get("lockingQuantity")),
        "cost_price": as_decimal(row.get("costPrice")),
        "cost_value": as_decimal(row.get("costValue")),
        "in_quantity_sum": as_decimal(row.get("inQuantitySum")),
        "out_quantity_sum": as_decimal(row.get("outQuantitySum")),
        "yesterday_quantity": as_decimal(row.get("yesterdayQuantity")),
        "week_quantity": as_decimal(row.get("weekQuantity")),
        "threeday_quantity": as_decimal(row.get("threedayQuantity")),
        "purchasing_quantity": as_decimal(row.get("purchasingQuantity")),
        "allocate_quantity": as_decimal(row.get("allocateQuantity")),
        "sales_return_quantity": as_decimal(row.get("salesReturnQuantity")),
        "last_stock_in_time": as_datetime(row.get("lastStockInTime")),
        "raw": _jsonb_safe(row),
    }


def sync_total_stock(db: Session, client: JkyWebClient) -> dict[str, int]:
    rows = client.fetch_total_stock()
    records = [_normalize_total_stock(r) for r in rows if _sku_key(r)]
    # 去重防御：接口若按仓库维度返回会含重复 sku_key，保留首行（与快照语义一致）
    deduped = {r["sku_key"]: r for r in records}
    stats = _snapshot_replace(db, JkyWebTotalStock, list(deduped.values()), "总库存")
    db.commit()
    return stats


def sync_warehouse_stock(db: Session, client: JkyWebClient) -> dict[str, int]:
    rows = client.fetch_warehouse_stock()
    records = [
        _normalize_warehouse_stock(r) for r in rows if _warehouse_key(r) and _sku_key(r)
    ]
    deduped = {(r["warehouse_key"], r["sku_key"]): r for r in records}
    stats = _snapshot_replace(db, JkyWebWarehouseStock, list(deduped.values()), "分仓库存")
    # 用分仓成本回填总库存的成本价/库存金额（allStockSkuList 不返回成本）
    db.execute(text("""
        UPDATE jky_web_total_stock t
        SET cost_price = agg.cost_price, cost_value = agg.cost_value
        FROM (
            SELECT sku_key,
                   SUM(cost_value) AS cost_value,
                   CASE WHEN SUM(current_quantity) > 0
                        THEN SUM(cost_value) / SUM(current_quantity)
                        ELSE NULL END AS cost_price
            FROM jky_web_warehouse_stock
            GROUP BY sku_key
        ) agg
        WHERE t.sku_key = agg.sku_key
    """))
    db.commit()
    return stats


# ---------------------------------------------------------------------------
# 登录态更新与状态查询
# ---------------------------------------------------------------------------

def update_session(db: Session, curl_text: str, actor: str = "system") -> dict[str, Any]:
    """解析用户粘贴的 cURL → 加密落库 → 置连接为可用。"""
    auth = merge_curl_texts(curl_text)
    manager = JkyWebSessionManager(db)
    conn = manager.save_auth(auth, SESSION_ACTIVE)
    db.add(SyncLog(
        provider=PROVIDER, level="info", message="吉客云网页登录态已更新",
        data={"endpoints": list(auth.endpoint_urls.keys()), "hasCommonVerify": bool(auth.common_verify)},
    ))
    from app.core.audit import audit
    audit(db, actor, "jky_web.session_updated", "integration", conn.id,
          {"endpoints": list(auth.endpoint_urls.keys())})
    db.commit()
    return {
        "ok": True,
        "endpoints": list(auth.endpoint_urls.keys()),
        "hasCommonVerify": bool(auth.common_verify),
    }


def _sync_window() -> tuple[datetime, datetime]:
    end = datetime.now()
    start = end - timedelta(days=settings.JKY_WEB_SYNC_LOOKBACK_DAYS)
    return start, end


def sync_all(
    db: Session, actor: str = "system", *, include_sales: bool = True
) -> dict[str, Any]:
    """吉客云 Web 数据同步编排。

    销售订单默认由三通道编排器处理；``include_sales=False`` 供每日 Web
    采购/库存任务使用，避免同一订单被两个调度任务重复拉取。
    """
    manager = JkyWebSessionManager(db)
    job = start_sync_job(db, PROVIDER, "daily_sync")
    stats: dict[str, Any] = {"modules": {}}
    errors: list[str] = []
    session_status = SESSION_ACTIVE

    try:
        auth = manager.load_auth()  # Session Check：未配置直接失败
    except JkyWebError as exc:
        finish_sync_job(db, job, "failed", {}, str(exc))
        manager.set_status(SESSION_NEED_LOGIN, str(exc))
        db.commit()
        return {"status": "need_login", "error": str(exc)}

    try:
        client = JkyWebClient(auth)
    except JkyWebError as exc:
        finish_sync_job(db, job, "failed", {}, str(exc))
        manager.set_status(SESSION_ERROR, str(exc))
        db.commit()
        return {"status": "failed", "error": str(exc)}

    start, end = _sync_window()

    def _run(step: str, fn) -> None:
        nonlocal session_status
        if session_status in (SESSION_NEED_LOGIN, SESSION_NEED_VERIFY):
            stats["modules"][step] = {"status": "skipped", "reason": "登录态不可用"}
            return
        try:
            result = fn()
            stats["modules"][step] = {"status": "success", **result}
            db.add(SyncLog(provider=PROVIDER, level="info", sync_job_id=job.id,
                           message=f"{step} 同步完成", data={"stats": _jsonb_safe(result)}))
            db.commit()
        except JkyWebError as exc:
            stats["modules"][step] = {"status": "failed", "error": str(exc)[:500]}
            errors.append(f"{step}: {exc}")
            db.rollback()
            if exc.kind == "need_login":
                session_status = SESSION_NEED_LOGIN
            elif exc.kind == "need_user_verify":
                session_status = SESSION_NEED_VERIFY
            db.add(SyncLog(provider=PROVIDER, level="error", sync_job_id=job.id,
                           message=f"{step} 同步失败：{exc}", data={"kind": exc.kind}))
            db.commit()

    if include_sales:
        _run("sales_orders", lambda: sync_sales_orders(db, client, start, end))
        _run("sales_details", lambda: sync_sales_details(db, client, start, end))
    _run("stockin", lambda: sync_stockin(db, client, start, end))
    _run("total_stock", lambda: sync_total_stock(db, client))
    _run("warehouse_stock", lambda: sync_warehouse_stock(db, client))

    # 1688 备注可能先于吉客云入库单到达；吉客云同步完成后重新核验一次，
    # 只有本地存在唯一入库单号才自动建 confirmed 链路。
    from app.services.alibaba1688_remark_match_service import run_verified_remark_match

    stats["remarkMatch"] = run_verified_remark_match(db, actor=actor)

    # V2 主数据：销售客户、采购入库供应商同步后立即物化 canonical partner FK。
    try:
        from app.services.partner_master_service import rebuild_partner_master

        stats["partnerMaster"] = rebuild_partner_master(
            db,
            actor=actor,
            run_payment_match=False,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        errors.append(f"统一往来主体: {exc}")
        stats["partnerMaster"] = {"status": "failed", "error": str(exc)[:500]}

    # 状态机：登录态问题优先；否则有失败→partial，全成功→success
    if session_status == SESSION_NEED_LOGIN:
        status = "need_login"
        manager.set_status(SESSION_NEED_LOGIN, "；".join(errors)[:500])
    elif session_status == SESSION_NEED_VERIFY:
        status = "need_user_verify"
        manager.set_status(SESSION_NEED_VERIFY, "；".join(errors)[:500])
    elif errors:
        status = "partial_success"
        manager.set_status(SESSION_ERROR, "；".join(errors)[:500])
    else:
        status = "success"
        manager.set_status(SESSION_ACTIVE)
    stats["window"] = {"start": start.isoformat(), "end": end.isoformat()}
    finish_sync_job(db, job, status, stats, "" if not errors else "；".join(errors)[:500])
    db.commit()
    return {"status": status, "stats": stats, "errors": errors}


def sync_status(db: Session) -> dict[str, Any]:
    """前端状态卡片：连接/登录态/最近同步/各表行数。只读本地库。"""
    manager = JkyWebSessionManager(db)
    conn = manager.connection()
    connection_status = conn.status if conn else "unconfigured"
    last_job = (
        db.query(SyncJob)
        .filter(SyncJob.provider == PROVIDER)
        .order_by(SyncJob.id.desc())
        .first()
    )
    counts = {
        "salesOrders": db.query(func.count(JkyWebSalesOrder.id)).scalar() or 0,
        "salesItems": db.query(func.count(JkyWebSalesOrderItem.id)).scalar() or 0,
        "stockinOrders": db.query(func.count(JkyWebStockinOrder.id)).scalar() or 0,
        "stockinItems": db.query(func.count(JkyWebStockinItem.id)).scalar() or 0,
        "totalStockSkus": db.query(func.count(JkyWebTotalStock.id)).scalar() or 0,
        "warehouseStockRows": db.query(func.count(JkyWebWarehouseStock.id)).scalar() or 0,
    }
    meta = dict(conn.meta or {}) if conn else {}
    return {
        "adapter": settings.JKY_ADAPTER,
        "status": connection_status,
        "sessionUpdatedAt": meta.get("sessionUpdatedAt"),
        "lastSyncAt": last_job.finished_at.isoformat() if last_job and last_job.finished_at else None,
        "lastSyncStatus": last_job.status if last_job else None,
        "lastSyncStats": (last_job.stats or {}) if last_job else {},
        "errorSummary": (conn.error_summary or "") if conn else "",
        "counts": counts,
    }
