"""经营看板（规格 6）：销售趋势 / 平台店铺排行 / SKU 排行 / 订单 / 售后 / 库存。

原则：
- 页面只查本地库（吉客云同步副本），不触发外部查询（规格 5）
- 数据未落地时如实返回空集，不伪造数字
- 全部金额 Decimal，输出 str
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.catalog import Product, ProductSku, Store, Warehouse
from app.models.consumable import Consumable, ConsumableSkuMapping
from app.models.sales import AftersalesOrder, SalesOrder, SalesOrderItem
from app.models.tax import TaxAccountingCategoryRule
from app.services.inbound_cost_service import resolve_sales_sku_id, sales_sku_lookup, weighted_inbound_costs
from app.services.inventory_position_service import current_positions
from app.services.sales_scope import deal_orders_condition
from app.utils.money import quantize, to_decimal


def _money(value: Decimal | None, *, fine: bool = False) -> str | None:
    """金额输出：默认分位；fine=True 输出 4 位小数。

    趋势/排行等会被前端逐日、逐行汇总的接口用 fine=True：若先舍入到分再相加，
    月合计会与月结（整月一次舍入）差 0.01~0.02，属假性对不上。展示仍由前端格式化为 2 位。
    """
    if value is None:
        return None
    step = Decimal("0.0001") if fine else Decimal("0.01")
    return f"{quantize(to_decimal(value), step):f}"


def _quantity(value: Decimal | None) -> str | None:
    return f"{to_decimal(value):f}" if value is not None else None


def _tax_rule_label(row: TaxAccountingCategoryRule | None) -> str:
    if row is None:
        return ""
    return f"{row.category_name} · {row.item_name}"


def _tax_rule_map(db: Session) -> dict[int, TaxAccountingCategoryRule]:
    return {row.id: row for row in db.query(TaxAccountingCategoryRule).all()}


def _valid_sales():
    """业绩口径：见 sales_scope 成交口径（待发/已发/待确认收货/已完成计入）。"""
    return deal_orders_condition()


def _range_conds(start: date | None, end: date | None) -> list[Any]:
    """ordered_at 闭区间过滤（含 end 当天；end 缺省不设上限），叠加业绩口径。"""
    conds = [_valid_sales()]
    if start is not None:
        conds.append(SalesOrder.ordered_at >= start)
    if end is not None:
        conds.append(SalesOrder.ordered_at < end + timedelta(days=1))
    return conds


def _sales_order_cost_parts(
    db: Session,
    orders: list[SalesOrder],
    *,
    as_of: date | datetime,
) -> tuple[dict[int, Decimal], set[int], set[int]]:
    """订单成本明细拆解：返回（已覆盖成本, 缺 SKU/数量/入库成本的订单, 有明细的订单）。

    销售导入文件中的成本字段只作为原始参考，不参与销售分析计算。销售明细
    先按唯一货品编码补齐 SKU，再使用截止报告期的本系统入库明细单价。
    """
    order_ids = [order.id for order in orders]
    if not order_ids:
        return {}, set(), set()
    items = (
        db.query(SalesOrderItem)
        .filter(SalesOrderItem.order_id.in_(order_ids))
        .order_by(SalesOrderItem.order_id, SalesOrderItem.id)
        .all()
    )
    lookup = sales_sku_lookup(db)
    item_rows: list[tuple[int, int | None, Decimal | None]] = []
    sku_ids: set[int] = set()
    for item in items:
        sku_id = resolve_sales_sku_id(item.sku_id, item.sku_code, lookup)
        quantity = to_decimal(item.quantity) if item.quantity is not None else None
        item_rows.append((item.order_id, sku_id, quantity))
        if sku_id is not None:
            sku_ids.add(sku_id)
    inbound_costs = weighted_inbound_costs(db, as_of=as_of, sku_ids=sku_ids)
    costs: dict[int, Decimal] = {}
    has_invalid_item: set[int] = set()
    for order_id, sku_id, quantity in item_rows:
        if sku_id is None or quantity is None or sku_id not in inbound_costs:
            has_invalid_item.add(order_id)
            continue
        costs[order_id] = costs.get(order_id, Decimal("0")) + inbound_costs[sku_id] * quantity
    item_order_ids = {order_id for order_id, _sku_id, _quantity in item_rows}
    return costs, has_invalid_item, item_order_ids


def sales_order_item_costs(
    db: Session,
    items: list[SalesOrderItem],
    *,
    as_of: date | datetime,
) -> dict[int, Decimal | None]:
    """逐行货品成本：销售数量 × 该 SKU 的入库加权单价，缺 SKU/数量/入库成本的行返回 None。

    与订单成本（sales_order_costs_partial）共用同一套 SKU 解析与入库单价，
    保证 Σ 行成本 = 订单成本（已覆盖部分）；销售明细导出按此把订单级的实付、
    成本拆到每个子 SKU 行上。
    """
    if not items:
        return {}
    lookup = sales_sku_lookup(db)
    resolved: list[tuple[int, int | None, Decimal | None]] = []
    sku_ids: set[int] = set()
    for item in items:
        sku_id = resolve_sales_sku_id(item.sku_id, item.sku_code, lookup)
        quantity = to_decimal(item.quantity) if item.quantity is not None else None
        resolved.append((item.id, sku_id, quantity))
        if sku_id is not None:
            sku_ids.add(sku_id)
    inbound_costs = weighted_inbound_costs(db, as_of=as_of, sku_ids=sku_ids)
    return {
        item_id: inbound_costs[sku_id] * quantity
        if sku_id is not None and quantity is not None and sku_id in inbound_costs
        else None
        for item_id, sku_id, quantity in resolved
    }


def sales_order_costs(
    db: Session,
    orders: list[SalesOrder],
    *,
    as_of: date | datetime,
) -> dict[int, Decimal | None]:
    """按入库单加权成本计算每张销售订单的货品成本（销售明细口径）。

    任何明细无法匹配 SKU、数量或入库成本缺失时，该订单成本保持为空，
    供销售明细逐单核对使用；业绩总览与销售明细共用本函数保证口径一致。
    """
    costs, invalid_orders, item_order_ids = _sales_order_cost_parts(db, orders, as_of=as_of)
    return {
        order.id: costs[order.id].quantize(Decimal("0.0001"))
        if order.id in item_order_ids and order.id in costs and order.id not in invalid_orders
        else None
        for order in orders
    }


def sales_order_costs_partial(
    db: Session,
    orders: list[SalesOrder],
    *,
    as_of: date | datetime,
) -> dict[int, tuple[Decimal | None, bool]]:
    """聚合口径的订单成本：按已覆盖明细累计，缺成本的订单标记不完整。

    与月结/利润中心（profit.compute）的「已覆盖部分出数」保持一致，
    避免个别 SKU 缺成本时聚合毛利整段留空或与月结对不上；
    整单没有任何可计算明细时成本返回 None，不伪造 0 成本。
    """
    costs, invalid_orders, item_order_ids = _sales_order_cost_parts(db, orders, as_of=as_of)
    return {
        order.id: (
            costs[order.id].quantize(Decimal("0.0001")) if order.id in costs else None,
            order.id in invalid_orders or order.id not in item_order_ids,
        )
        for order in orders
    }


def sales_trend(db: Session, days: int = 30, start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    """区间内每日销售额/订单数/入库成本/毛利润。按 ordered_at 分组（本地库）。
    start 传入时按 [start, end] 闭区间，否则取近 N 天。
    个别订单缺入库成本时按已覆盖部分出数并置 costIncomplete，避免整段毛利留空。"""
    if start is not None:
        conds = _range_conds(start, end)
        as_of: date | datetime = (end + timedelta(days=1)) if end else datetime.now(timezone.utc)
    else:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        conds = [SalesOrder.ordered_at >= since, _valid_sales()]
        as_of = datetime.now(timezone.utc)
    orders = db.query(SalesOrder).filter(*conds).all()
    order_costs = sales_order_costs_partial(db, orders, as_of=as_of)
    grouped: dict[str, dict[str, Any]] = {}
    for order in orders:
        if order.ordered_at is None:
            continue
        day = order.ordered_at.date().isoformat()
        bucket = grouped.setdefault(day, {
            "orders": 0,
            "sales": Decimal("0"),
            "cost": Decimal("0"),
            "gross": Decimal("0"),
            "costIncomplete": False,
        })
        bucket["orders"] += 1
        sales = to_decimal(order.paid_amount) if order.paid_amount is not None else Decimal("0")
        bucket["sales"] += sales
        # 按已覆盖明细出成本（与月结同口径）；缺成本明细的订单标记不完整并由前端提示补充。
        # 毛利 = 销售额 − 已覆盖成本：整单没有可计算成本时仍计入销售额（成本按 0 累计），
        # 与月结 profit.compute 的「已覆盖部分出数」保持一致。
        cost, incomplete = order_costs.get(order.id, (None, True))
        if incomplete:
            bucket["costIncomplete"] = True
        bucket["gross"] += sales
        if cost is not None:
            bucket["cost"] += cost
            bucket["gross"] -= cost
    return [
        {
            "date": day,
            "orders": bucket["orders"],
            "salesAmount": _money(bucket["sales"], fine=True),
            "costAmount": _money(bucket["cost"], fine=True),
            "grossProfit": _money(bucket["gross"], fine=True),
            "costIncomplete": bucket["costIncomplete"],
        }
        for day, bucket in sorted(grouped.items())
    ]


def platform_ranking(db: Session, start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    """平台/店铺销售排行；货品成本来自本系统入库单。无平台字段的订单归入 unknown。
    个别订单缺入库成本时按已覆盖部分出数并置 costIncomplete。"""
    orders = db.query(SalesOrder).filter(*_range_conds(start, end)).all()
    as_of: date | datetime = (end + timedelta(days=1)) if end else datetime.now(timezone.utc)
    order_costs = sales_order_costs_partial(db, orders, as_of=as_of)
    grouped: dict[str, dict[str, Any]] = {}
    for order in orders:
        name = order.platform or "unknown"
        bucket = grouped.setdefault(name, {
            "orders": 0,
            "sales": Decimal("0"),
            "cost": Decimal("0"),
            "gross": Decimal("0"),
            "costIncomplete": False,
        })
        bucket["orders"] += 1
        sales = to_decimal(order.paid_amount) if order.paid_amount is not None else Decimal("0")
        bucket["sales"] += sales
        cost, incomplete = order_costs.get(order.id, (None, True))
        if incomplete:
            bucket["costIncomplete"] = True
        # 同 sales_trend：毛利始终含销售额，成本只减已覆盖部分。
        bucket["gross"] += sales
        if cost is not None:
            bucket["cost"] += cost
            bucket["gross"] -= cost
    return [
        {
            "platform": platform,
            "orders": bucket["orders"],
            "salesAmount": _money(bucket["sales"], fine=True),
            "costAmount": _money(bucket["cost"], fine=True),
            "grossProfit": _money(bucket["gross"], fine=True),
            "costIncomplete": bucket["costIncomplete"],
        }
        for platform, bucket in sorted(grouped.items(), key=lambda item: item[1]["sales"], reverse=True)
    ]


def sku_ranking(db: Session, limit: int = 20, start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    """SKU 销售额排行：把订单实付按各行成本占比分摊到 SKU。

    吉客云行金额存在 0 值与负数，不能作为收入基数；分摊后各 SKU 金额合计
    等于业绩口径销售额。个别行缺入库成本时退化为按销售数量占比分摊。
    """
    orders = db.query(SalesOrder).filter(*_range_conds(start, end)).all()
    order_ids = [order.id for order in orders]
    if not order_ids:
        return []
    items = (
        db.query(SalesOrderItem)
        .filter(SalesOrderItem.order_id.in_(order_ids))
        .order_by(SalesOrderItem.order_id, SalesOrderItem.id)
        .all()
    )
    lookup = sales_sku_lookup(db)
    parsed: list[tuple[SalesOrderItem, int | None, Decimal | None]] = []
    sku_ids: set[int] = set()
    for item in items:
        sku_id = resolve_sales_sku_id(item.sku_id, item.sku_code, lookup)
        quantity = to_decimal(item.quantity) if item.quantity is not None else None
        parsed.append((item, sku_id, quantity))
        if sku_id is not None:
            sku_ids.add(sku_id)
    as_of = (
        datetime.now(timezone.utc)
        if end is None
        else datetime.combine(end + timedelta(days=1), datetime.min.time())
    )
    inbound_costs = weighted_inbound_costs(db, as_of=as_of, sku_ids=sku_ids)

    weights: dict[int, list[tuple[SalesOrderItem, Decimal]]] = {}
    for item, sku_id, quantity in parsed:
        unit_cost = inbound_costs.get(sku_id) if sku_id is not None else None
        if unit_cost is not None and quantity is not None:
            weight = unit_cost * quantity
        elif quantity is not None:
            weight = quantity
        elif unit_cost is not None:
            weight = unit_cost
        else:
            weight = Decimal("0")
        weights.setdefault(item.order_id, []).append((item, weight))

    totals: dict[tuple[str, str], dict[str, Any]] = {}
    for order in orders:
        rows = weights.get(order.id)
        if not rows:
            continue
        paid = to_decimal(order.paid_amount) if order.paid_amount is not None else Decimal("0")
        total_weight = sum((weight for _item, weight in rows), Decimal("0"))
        for item, weight in rows:
            share = paid * (weight / total_weight) if total_weight > 0 else paid / len(rows)
            key = (item.sku_code or "", item.goods_name or "")
            bucket = totals.setdefault(key, {
                "skuCode": key[0], "goodsName": key[1], "orders": 0, "amount": Decimal("0"),
            })
            bucket["orders"] += 1
            bucket["amount"] += share
    ranked = sorted(totals.values(), key=lambda row: row["amount"], reverse=True)[:limit]
    return [
        {
            "skuCode": row["skuCode"],
            "goodsName": row["goodsName"],
            "orders": row["orders"],
            "salesAmount": _money(row["amount"], fine=True),
        }
        for row in ranked
    ]


def inventory_summary(db: Session) -> dict[str, Any]:
    """库存概览：独立运算 Σ采购入库 − Σ销售出库，仓库按本系统仓库档案归属。"""
    skus = db.query(ProductSku).count()
    positions = current_positions(db)
    last_at = positions["last_document_at"]
    if last_at is None:
        return {"skuCount": skus, "lastDocumentAt": None, "totalQuantity": None,
                "byWarehouse": [], "note": "暂无出入库单据；导入采购入库/销售出库单后自动累计"}
    by_warehouse_values: dict[int | None, dict[int, Decimal]] = {}
    for sku_id, per_warehouse in positions["by_sku_warehouse"].items():
        for warehouse_id, quantity in per_warehouse.items():
            by_warehouse_values.setdefault(warehouse_id, {})[sku_id] = quantity
    wh_names = {row.id: row.name for row in db.query(Warehouse).all()}
    total = sum(positions["by_sku"].values(), Decimal("0"))
    by_warehouse = [
        {
            "warehouseId": warehouse_id,
            "warehouseName": wh_names.get(warehouse_id) or "未映射仓库",
            "quantity": _quantity(sum(values.values(), Decimal("0"))),
            "skus": len(values),
        }
        for warehouse_id, values in sorted(by_warehouse_values.items(), key=lambda item: (item[0] is None, item[0] or 0))
    ]
    return {
        "skuCount": skus,
        "lastDocumentAt": last_at.isoformat(),
        "totalQuantity": _quantity(total),
        "byWarehouse": by_warehouse,
        "positionSource": positions["source"],
        "appliedDocumentCount": positions["applied_document_count"],
    }


def inventory_skus(db: Session, search: str = "", limit: int = 1000) -> list[dict[str, Any]]:
    """SKU 级库存清单：独立运算（采购入库 − 销售出库）的总量与仓库分布。

    返回全部 SKU 档案（含无单据的），带 hasMovement=false 标记；金额/数量输出 str。
    """
    positions = current_positions(db)
    last_at = positions["last_document_at"]
    agg = positions["by_sku_warehouse"]
    wh_names = {w.id: w.name for w in db.query(Warehouse).all()}

    q = (
        db.query(ProductSku, Product)
        .outerjoin(Product, Product.id == ProductSku.product_id)
        .order_by(ProductSku.sku_code, ProductSku.id)
    )
    term = search.strip()
    if term:
        pattern = f"%{term}%"
        q = q.filter(or_(
            ProductSku.sku_code.ilike(pattern),
            ProductSku.sku_name.ilike(pattern),
            ProductSku.barcode.ilike(pattern),
            Product.goods_name.ilike(pattern),
        ))

    out: list[dict[str, Any]] = []
    for sku, product in q.limit(limit).all():
        per = agg.get(sku.id, {})
        total = positions["by_sku"].get(sku.id, Decimal("0"))
        warehouses = [
            {"warehouseId": wid, "warehouseName": wh_names.get(wid) or "未映射仓库", "quantity": _quantity(qty)}
            for wid, qty in per.items()
        ]
        out.append({
            "skuId": sku.id,
            "jackyunSkuId": sku.jackyun_sku_id,
            "skuCode": sku.sku_code,
            "productType": sku.product_type or ("virtual_bundle" if sku.sku_code.upper().startswith("ES") else "single"),
            "skuName": sku.sku_name,
            "goodsName": product.goods_name if product else "",
            "barcode": sku.barcode,
            "unit": sku.unit,
            "status": sku.status,
            "quantity": _quantity(total),
            "hasMovement": sku.id in positions["seen_skus"],
            "warehouses": warehouses,
            "lastDocumentAt": last_at.isoformat() if last_at is not None else None,
        })
    return out


def list_products(db: Session, search: str = "", limit: int = 200) -> list[dict[str, Any]]:
    """吉客云商品/SKU 本地主档；供商品页和采购 SKU 选择器复用。"""
    q = (
        db.query(ProductSku, Product)
        .outerjoin(Product, Product.id == ProductSku.product_id)
        .order_by(ProductSku.sku_code, ProductSku.id)
    )
    term = search.strip()
    if term:
        pattern = f"%{term}%"
        q = q.filter(or_(
            ProductSku.sku_code.ilike(pattern),
            ProductSku.sku_name.ilike(pattern),
            ProductSku.barcode.ilike(pattern),
            Product.goods_name.ilike(pattern),
        ))
    tax_rules = _tax_rule_map(db)
    return [
        {
            "id": sku.id,
            "jackyunSkuId": sku.jackyun_sku_id,
            "skuCode": sku.sku_code,
            "productType": sku.product_type or ("virtual_bundle" if sku.sku_code.upper().startswith("ES") else "single"),
            "skuName": sku.sku_name,
            "goodsName": product.goods_name if product else "",
            "barcode": sku.barcode,
            "unit": sku.unit,
            "salePrice": _money(sku.sale_price),
            "defaultCost": _money(sku.default_cost),
            "costMode": sku.cost_mode or "fixed",
            "costTolerancePct": str(sku.cost_tolerance_pct) if sku.cost_tolerance_pct is not None else "0.0200",
            "taxCode": sku.tax_code or "",
            "taxCategoryRuleId": sku.tax_category_rule_id,
            "taxCategoryRuleName": _tax_rule_label(tax_rules.get(sku.tax_category_rule_id)),
            # 货品级耗材策略沿用现有 JSONB 扩展字段，避免为一次业务规则变更增加迁移。
            "consumablePolicy": (sku.raw or {}).get("consumablePolicy") or "auto",
            "status": sku.status,
        }
        for sku, product in q.limit(limit).all()
    ]


def catalog_unified(db: Session, kind: str = "all", search: str = "", limit: int = 500) -> list[dict[str, Any]]:
    """统一货品档案：正品（吉客云 SKU）+ 耗材（本平台档案）合成一份列表。

    - kind=all/goods/consumable；search 命中编码/名称/条码。
    - 正品行库存由本系统独立运算：采购入库 − 销售出库；
    - 耗材行返回 自有仓/工厂/在途 三口径 + 安全库存预警 + 关联正品。
    - 条形码允许正品/耗材相同：系统内以 (kind, id) 独立 ID 区分，不以条码作主键。
    """
    term = search.strip()
    pattern = f"%{term}%" if term else None
    items: list[dict[str, Any]] = []
    tax_rules = _tax_rule_map(db)

    if kind in {"all", "goods"}:
        positions = current_positions(db)
        agg = positions["by_sku"]
        q = (
            db.query(ProductSku, Product)
            .outerjoin(Product, Product.id == ProductSku.product_id)
            .order_by(ProductSku.sku_code, ProductSku.id)
        )
        if pattern:
            q = q.filter(or_(
                ProductSku.sku_code.ilike(pattern),
                ProductSku.sku_name.ilike(pattern),
                ProductSku.barcode.ilike(pattern),
                Product.goods_name.ilike(pattern),
                Product.category.ilike(pattern),
            ))
        for sku, product in q.limit(limit).all():
            items.append({
                "kind": "goods",
                "id": sku.id,
                "code": sku.sku_code,
                "jackyunSkuId": sku.jackyun_sku_id,
                "name": sku.sku_name or (product.goods_name if product else ""),
                "goodsName": product.goods_name if product else "",
                "barcode": sku.barcode or "",
                "unit": sku.unit or "",
                "category": sku.product_type or "single",
                "goodsCategory": (product.category if product else "") or ((product.raw or {}).get("cateName", "") if product else "") or ((sku.raw or {}).get("goodsCategory", "")) or ((sku.raw or {}).get("cateName", "")),
                "status": sku.status,
                "stockOwn": _quantity(agg.get(sku.id)),
                "stockFactory": None,
                "stockTransit": None,
                "minStock": None,
                "lowStock": False,
                "hasMovement": sku.id in positions["seen_skus"],
                "linkedSkus": [],
                "costMode": sku.cost_mode or "fixed",
                "costTolerancePct": str(sku.cost_tolerance_pct) if sku.cost_tolerance_pct is not None else "0.0200",
                "taxCode": sku.tax_code or "",
                "taxCategoryRuleId": sku.tax_category_rule_id,
                "taxCategoryRuleName": _tax_rule_label(tax_rules.get(sku.tax_category_rule_id)),
                "salePrice": _money(sku.sale_price),
                "defaultCost": _money(sku.default_cost),
            })

    if kind in {"all", "consumable"}:
        cq = db.query(Consumable).order_by(Consumable.code, Consumable.id)
        if pattern:
            cq = cq.filter(or_(
                Consumable.code.ilike(pattern),
                Consumable.name.ilike(pattern),
                Consumable.barcode.ilike(pattern),
            ))
        consumables = cq.limit(limit).all()
        link_rows = (
            db.query(ConsumableSkuMapping, ProductSku)
            .join(ProductSku, ProductSku.id == ConsumableSkuMapping.sku_id)
            .filter(ConsumableSkuMapping.consumable_id.in_([c.id for c in consumables]))
            .all()
        ) if consumables else []
        links: dict[int, list[dict[str, Any]]] = {}
        for mapping, sku in link_rows:
            links.setdefault(mapping.consumable_id, []).append(
                {"skuId": sku.id, "skuCode": sku.sku_code, "skuName": sku.sku_name}
            )
        for row in consumables:
            available = to_decimal(row.stock_qty) + to_decimal(row.factory_qty)
            min_qty = to_decimal(row.min_stock_qty)
            items.append({
                "kind": "consumable",
                "id": row.id,
                "code": row.code,
                "name": row.name,
                "goodsName": "",
                "barcode": row.barcode or "",
                "unit": row.unit,
                "category": row.category,
                "goodsCategory": row.category,
                "status": row.status,
                "stockOwn": _quantity(row.stock_qty),
                "stockFactory": _quantity(row.factory_qty),
                "stockTransit": _quantity(row.transit_qty),
                "minStock": _quantity(row.min_stock_qty),
                "lowStock": (min_qty > 0 and available <= min_qty)
                or to_decimal(row.stock_qty) < 0
                or to_decimal(row.factory_qty) < 0,
                "hasSnapshot": None,
                "linkedSkus": links.get(row.id, []),
                "costMode": None,
                "costTolerancePct": None,
                "taxCode": row.tax_code or "",
                "taxCategoryRuleId": row.tax_category_rule_id,
                "taxCategoryRuleName": _tax_rule_label(tax_rules.get(row.tax_category_rule_id)),
                "salePrice": None,
                "defaultCost": None,
                "purchaseUnitCost": _money(row.purchase_unit_cost),
            })

    items.sort(key=lambda r: (r["kind"], r["code"]))
    return items


def overview_metrics(db: Session) -> dict[str, Any]:
    """总览首屏 9 指标（规格 4）。全部来自本地库聚合，数据为空如实 None。"""
    paid_orders = (
        db.query(SalesOrder)
        .filter(deal_orders_condition(), SalesOrder.paid_amount.isnot(None))
        .all()
    )
    sales_amount = sum((to_decimal(o.paid_amount) for o in paid_orders), Decimal("0"))
    order_count = db.query(SalesOrder).filter(deal_orders_condition()).count()

    refunds = db.query(AftersalesOrder).filter(AftersalesOrder.type == "refund").all()
    refund_amount = sum((to_decimal(r.refund_amount) for r in refunds), Decimal("0"))
    net_sales = sales_amount - refund_amount
    refund_rate = None
    if sales_amount > 0:
        refund_rate = f"{((refund_amount / sales_amount) * 100).quantize(Decimal('0.01'))}"

    from app.models.profit import ProfitSnapshot
    from app.services import reconciliation as rc

    recon = rc.overview(db)
    receivable = to_decimal(recon["receivable"])
    received = to_decimal(recon["received"])
    gross = db.query(ProfitSnapshot).filter(ProfitSnapshot.gross_profit.isnot(None)).order_by(ProfitSnapshot.id.desc()).first()

    return {
        "salesAmount": _money(sales_amount) if order_count else None,
        "netSales": _money(net_sales) if order_count else None,
        "orderCount": order_count if order_count else None,
        "refundRate": refund_rate,
        "grossProfit": _money(gross.gross_profit) if gross else None,
        "receivable": _money(receivable) if receivable else None,
        "received": _money(received) if received else None,
        "pendingReceive": _money(receivable - received) if receivable else None,
    }


def list_orders(db: Session, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    q = db.query(SalesOrder).order_by(SalesOrder.ordered_at.desc().nullslast(), SalesOrder.id.desc())
    if status:
        q = q.filter(SalesOrder.order_status == status)
    rows = q.limit(limit).all()
    # Bulk fetch stores to avoid N+1 (was one db.get per order)
    store_ids = {o.store_id for o in rows if o.store_id}
    store_map: dict[int, Store] = {}
    if store_ids:
        store_map = {s.id: s for s in db.query(Store).filter(Store.id.in_(store_ids)).all()}
    order_ids = [o.id for o in rows]
    item_map: dict[int, dict[str, Any]] = {}
    item_counts: dict[int, int] = {}
    if order_ids:
        item_rows = (
            db.query(SalesOrderItem)
            .filter(SalesOrderItem.order_id.in_(order_ids))
            .order_by(SalesOrderItem.order_id, SalesOrderItem.id)
            .all()
        )
        for item in item_rows:
            item_counts[item.order_id] = item_counts.get(item.order_id, 0) + 1
            summary = item_map.setdefault(item.order_id, {
                "name": (item.goods_name or "").strip() or (item.sku_code or "").strip(),
                "quantity": Decimal("0"),
                "has_quantity": False,
            })
            if item.quantity is not None:
                summary["quantity"] += to_decimal(item.quantity)
                summary["has_quantity"] = True
    out = []
    for o in rows:
        store = store_map.get(o.store_id) if o.store_id else None
        item = item_map.get(o.id, {})
        out.append({
            "id": o.id, "orderNo": o.order_no, "platform": o.platform,
            "storeName": store.name if store else "",
            "orderStatus": o.order_status, "payStatus": o.pay_status,
            "orderAmount": _money(o.order_amount), "paidAmount": _money(o.paid_amount),
            "itemName": item.get("name", ""),
            "quantity": _quantity(item["quantity"]) if item.get("has_quantity") else None,
            "itemCount": item_counts.get(o.id, 0),
            "orderedAt": o.ordered_at.isoformat() if o.ordered_at else None,
        })
    return out


def list_aftersales(db: Session, limit: int = 200) -> list[dict[str, Any]]:
    rows = db.query(AftersalesOrder).order_by(AftersalesOrder.created_at_src.desc().nullslast(),
                                              AftersalesOrder.id.desc()).limit(limit).all()
    return [
        {
            "id": r.id, "aftersaleNo": r.aftersale_no, "orderNo": r.order_no,
            "type": r.type, "status": r.status,
            "refundAmount": _money(r.refund_amount), "reason": r.reason,
            "createdAt": r.created_at_src.isoformat() if r.created_at_src else None,
        }
        for r in rows
    ]
