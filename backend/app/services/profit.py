"""利润中心（规格 9）。

- 商品毛利 = 净销售收入 − 商品成本
- 净销售收入取订单客户实付金额，与月结/经营总览、销售明细共用同一口径
- 销售货品成本只取本系统采购入库单明细的加权平均成本
- 成本台账仍允许多种来源并存，供独立成本管理使用
- SKU、销量或入库成本缺失时不输出伪精确的商品成本和毛利
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.audit import audit
from app.models.catalog import ProductSku
from app.models.profit import CostSnapshot
from app.models.sales import SalesOrder, SalesOrderItem
from app.services.inbound_cost_service import resolve_sales_sku_id, sales_sku_lookup, weighted_inbound_costs
from app.services.monthly_core import month_bounds, refund_total
from app.services.sales_scope import deal_orders_condition
from app.utils.money import quantize, to_decimal

COST_PRIORITY = ("actual_cost", "purch_order_cost", "default_cost", "estimated_cost")
COST_LABEL = {
    "actual_cost": "实际采购结算成本",
    "purch_order_cost": "采购订单成本",
    "default_cost": "SKU默认成本",
    "estimated_cost": "暂估成本",
}
SOURCE_ALIASES = {
    "actual": "actual_cost", "settlement": "actual_cost", "actual_cost": "actual_cost",
    "purch": "purch_order_cost", "order": "purch_order_cost", "purch_order_cost": "purch_order_cost",
    "default": "default_cost", "default_cost": "default_cost",
    "estimated": "estimated_cost", "estimated_cost": "estimated_cost",
}


def effective_cost(snapshot: CostSnapshot) -> tuple[Decimal | None, str | None]:
    """按优先级取单条快照的有效成本。"""
    for field in COST_PRIORITY:
        value = getattr(snapshot, field)
        if value is not None:
            return to_decimal(value), field
    return None, None


def gross_profit(net_sales: Decimal | None, cost: Decimal | None) -> Decimal | None:
    """净销售或成本缺失时返回 None，禁止假装精确。"""
    if net_sales is None or cost is None:
        return None
    return quantize(net_sales - cost, Decimal("0.01"))


def _combined_cost(
    rows: list[CostSnapshot], sku_default: Decimal | None = None
) -> tuple[dict[str, Decimal | None], Decimal | None, str | None]:
    """合并同一 SKU/账期的多来源记录，每个来源使用其最新版本。"""
    values: dict[str, Decimal | None] = {field: None for field in COST_PRIORITY}
    ordered = sorted(rows, key=lambda row: (row.version or 0, row.id or 0), reverse=True)
    for field in COST_PRIORITY:
        for row in ordered:
            value = getattr(row, field)
            if value is not None:
                values[field] = to_decimal(value)
                break
    if values["default_cost"] is None and sku_default is not None:
        values["default_cost"] = to_decimal(sku_default)
    for field in COST_PRIORITY:
        if values[field] is not None:
            return values, values[field], field
    return values, None, None


def upsert_cost(
    db: Session,
    *,
    sku_id: int,
    period_year: int,
    period_month: int,
    actual_cost: str | None = None,
    purch_order_cost: str | None = None,
    default_cost: str | None = None,
    estimated_cost: str | None = None,
    source: str | None = None,
    actor: str = "system",
) -> CostSnapshot:
    """按来源幂等 upsert；不同来源并存，单一来源变更时版本号递增。"""
    if not (1 <= period_month <= 12):
        raise ValueError("非法账期")
    raw_values = {
        "actual_cost": actual_cost,
        "purch_order_cost": purch_order_cost,
        "default_cost": default_cost,
        "estimated_cost": estimated_cost,
    }
    provided = [field for field, value in raw_values.items() if value is not None]
    if len(provided) != 1:
        raise ValueError("每次必须且只允许登记一种来源的成本")
    chosen = provided[0]
    if source:
        canonical = SOURCE_ALIASES.get(source)
        if canonical is None:
            raise ValueError("未知成本来源")
        if canonical != chosen:
            raise ValueError("成本来源与填写字段不一致")
    sku = db.get(ProductSku, sku_id)
    if sku is None:
        raise ValueError("SKU 不存在，请先同步吉客云商品主档")
    value = to_decimal(raw_values[chosen])
    if value < 0:
        raise ValueError("成本不能为负数")

    candidates = (
        db.query(CostSnapshot)
        .filter_by(sku_id=sku_id, period_year=period_year, period_month=period_month)
        .order_by(CostSnapshot.version.desc(), CostSnapshot.id.desc())
        .all()
    )
    row = next(
        (item for item in candidates
         if SOURCE_ALIASES.get(item.source, item.source) == chosen
         or getattr(item, chosen) is not None),
        None,
    )
    if row:
        if getattr(row, chosen) != value:
            row.version = (row.version or 1) + 1
        for field in COST_PRIORITY:
            setattr(row, field, value if field == chosen else None)
        db.commit()
        audit(
            db, actor, "profit.cost.upsert", "cost_snapshots", row.id,
            {"skuId": sku_id, "period": f"{period_year}-{period_month:02d}",
             "source": chosen, "value": str(value), "version": row.version},
        )
        return row

    values = {field: value if field == chosen else None for field in COST_PRIORITY}
    row = CostSnapshot(
        sku_id=sku_id, period_year=period_year, period_month=period_month,
        source=chosen, version=1, **values,
    )
    db.add(row)
    db.commit()
    audit(
        db, actor, "profit.cost.create", "cost_snapshots", row.id,
        {"skuId": sku_id, "period": f"{period_year}-{period_month:02d}",
         "source": chosen, "value": str(value)},
    )
    return row


def list_costs(
    db: Session, period_year: int | None = None, period_month: int | None = None
) -> list[dict[str, Any]]:
    q = db.query(CostSnapshot)
    if period_year:
        q = q.filter(CostSnapshot.period_year == period_year)
    if period_month:
        q = q.filter(CostSnapshot.period_month == period_month)
    rows = q.order_by(
        CostSnapshot.period_year.desc(), CostSnapshot.period_month.desc(), CostSnapshot.sku_id
    ).all()
    grouped: dict[tuple[int, int, int], list[CostSnapshot]] = {}
    for row in rows:
        grouped.setdefault((row.sku_id, row.period_year, row.period_month), []).append(row)
    sku_ids = {key[0] for key in grouped}
    skus = (
        {sku.id: sku for sku in db.query(ProductSku).filter(ProductSku.id.in_(sku_ids)).all()}
        if sku_ids else {}
    )

    out = []
    for (sku_id, year, month), source_rows in grouped.items():
        sku = skus.get(sku_id)
        values, value, source = _combined_cost(source_rows, sku.default_cost if sku else None)
        out.append({
            "id": max(row.id for row in source_rows), "skuId": sku_id,
            "skuCode": sku.sku_code if sku else "", "skuName": sku.sku_name if sku else "",
            "period": f"{year}-{month:02d}",
            "values": {
                "actual": str(values["actual_cost"]) if values["actual_cost"] is not None else None,
                "purchOrder": str(values["purch_order_cost"]) if values["purch_order_cost"] is not None else None,
                "default": str(values["default_cost"]) if values["default_cost"] is not None else None,
                "estimated": str(values["estimated_cost"]) if values["estimated_cost"] is not None else None,
            },
            "effectiveCost": str(value) if value is not None else None,
            "effectiveSource": COST_LABEL[source] if source else None,
            "version": max(row.version or 1 for row in source_rows),
        })
    return out


def overview(db: Session) -> dict[str, Any]:
    """按唯一 SKU/账期统计成本覆盖；不同来源记录不重复计算覆盖。"""
    rows = db.query(CostSnapshot).all()
    counts: dict[str, int] = {
        "actual_cost": 0, "purch_order_cost": 0, "default_cost": 0,
        "estimated_cost": 0, "missing": 0,
    }
    grouped: dict[tuple[int, int, int], list[CostSnapshot]] = {}
    for row in rows:
        grouped.setdefault((row.sku_id, row.period_year, row.period_month), []).append(row)
    covered_skus: set[int] = set()
    for (sku_id, _year, _month), source_rows in grouped.items():
        _, value, source = _combined_cost(source_rows)
        counts[source or "missing"] += 1
        if value is not None:
            covered_skus.add(sku_id)

    sku_rows = db.query(ProductSku).all()
    for sku in sku_rows:
        if sku.id not in covered_skus and sku.default_cost is not None:
            counts["default_cost"] += 1
            covered_skus.add(sku.id)
    counts["missing"] += max(len(sku_rows) - len(covered_skus), 0)
    return {
        "skuCount": len(sku_rows), "snapshotCount": len(rows), "coverage": counts,
        "contributionProfitEnabled": False,
    }


def compute(db: Session, period_year: int, period_month: int) -> dict[str, Any]:
    """按账期计算商品毛利；单位成本乘销量，缺失项明确返回。"""
    if not (1 <= period_month <= 12):
        raise ValueError("非法账期")
    # 与经营总览/月结共用业务时区自然月边界，禁止 UTC 月份造成月初/月末跨期。
    period_start, next_start = month_bounds(period_year, period_month)
    orders = (
        db.query(SalesOrder)
        .filter(deal_orders_condition())
        .filter(SalesOrder.ordered_at >= period_start, SalesOrder.ordered_at < next_start)
        .all()
    )
    # 收入基数取客户实付金额：吉客云导出的行金额/优惠与实付对不上（同一单甚至出现负数行），
    # 只有实付是真实成交金额，且与月结/经营总览、销售明细保持同一口径。
    gross_sales = sum(
        (to_decimal(row.paid_amount) for row in orders if row.paid_amount is not None),
        Decimal("0"),
    )
    net_sales = gross_sales - refund_total(db, period_start, next_start)
    items = (
        db.query(SalesOrderItem)
        .filter(SalesOrderItem.order_id.in_([row.id for row in orders]))
        .all()
    )

    sku_lookup = sales_sku_lookup(db)
    sku_quantities: dict[int, Decimal] = {}
    unmapped_items: list[int] = []
    quantity_missing_items: list[int] = []
    for item in items:
        sku_id = resolve_sales_sku_id(item.sku_id, item.sku_code, sku_lookup)
        if not sku_id:
            unmapped_items.append(item.id)
        elif item.quantity is None:
            quantity_missing_items.append(item.id)
        else:
            sku_quantities[sku_id] = sku_quantities.get(sku_id, Decimal("0")) + item.quantity

    skus = (
        {sku.id: sku for sku in db.query(ProductSku)
         .filter(ProductSku.id.in_(sku_quantities.keys())).all()}
        if sku_quantities else {}
    )
    weighted_costs = weighted_inbound_costs(
        db,
        as_of=next_start,
        sku_ids=set(sku_quantities),
    )

    goods_cost = Decimal("0")
    covered_skus = 0
    missing_skus: set[int] = set()
    missing_detail: list[dict[str, Any]] = []
    for sku_id, quantity in sku_quantities.items():
        sku = skus.get(sku_id)
        # 销售货品成本只认账期截止前的本系统采购入库事实，不再用销售导入文件、
        # 采购订单成本或货品档案默认成本补齐，否则会把不同业务口径混在一起。
        unit_cost = weighted_costs.get(sku_id) if sku is not None else None
        if unit_cost is None:
            missing_skus.add(sku_id)
            missing_detail.append({
                "skuId": sku_id,
                "skuCode": sku.sku_code if sku else "",
                "skuName": sku.sku_name if sku else "",
                "quantity": str(quantity),
            })
            continue
        covered_skus += 1
        goods_cost += unit_cost * quantity

    net_sales = quantize(net_sales, Decimal("0.01"))
    goods_cost = quantize(goods_cost, Decimal("0.01"))
    # 有销量就必须出毛利：个别 SKU 缺成本时先按已覆盖部分计算并给出 warning，
    # 由用户补录入库成本后重算；完全没有任何成本可用时 error 报错，不出伪数。
    can_estimate = covered_skus > 0
    has_items = bool(items)
    incomplete = bool(missing_skus or unmapped_items or quantity_missing_items)
    warning: str | None = None
    error: str | None = None
    if has_items and not can_estimate:
        error = "本月销售涉及的所有 SKU 都缺采购入库成本，毛利无法计算，请补充采购入库成本后重新月结。"
    elif incomplete:
        parts: list[str] = []
        if missing_detail:
            names = "、".join(
                f"{row['skuCode']} {row['skuName']}".strip() for row in missing_detail[:5]
            )
            parts.append(
                f"{len(missing_detail)} 个 SKU 缺采购入库成本（{names}"
                f"{' 等' if len(missing_detail) > 5 else ''}）"
            )
        if unmapped_items:
            parts.append(f"{len(unmapped_items)} 行明细未匹配到货品档案")
        if quantity_missing_items:
            parts.append(f"{len(quantity_missing_items)} 行明细缺销售数量")
        warning = (
            "；".join(parts)
            + "。毛利只含已覆盖部分，实际毛利会更低，请补充后在月结重新计算。"
        )
    return {
        "period": f"{period_year}-{period_month:02d}",
        "netSales": str(net_sales) if orders else None,
        "goodsCost": str(goods_cost) if has_items and can_estimate else None,
        "grossProfit": str(gross_profit(net_sales, goods_cost))
        if has_items and can_estimate else None,
        "costMissingSkus": sorted(missing_skus),
        "costMissingDetail": missing_detail,
        "unmappedItems": sorted(unmapped_items),
        "quantityMissingItems": sorted(quantity_missing_items),
        "costMissing": incomplete,
        "warning": warning,
        "error": error,
        "note": "净销售收入统一取成交单客户实付金额减当月退款；"
                "货品成本按本系统采购入库明细的加权平均单价乘销售数量计算；"
                "个别 SKU 缺入库成本时按已覆盖部分出毛利并给出 warning，"
                "全部缺成本时 error 报错不出数。",
    }
