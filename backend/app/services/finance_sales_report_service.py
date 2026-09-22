"""可配置月度销售汇总。

原则：
- 字段、别名、顺序保存在数据库模板中；后台月度任务与前端共用同一模板。
- 有效销售订单口径 = 成交口径（sales_scope）：待发/已发/待确认收货/已完成计入，
  关闭/取消/作废/退货/退款/待审核/待付款单不计入。
- 销售金额取订单客户实付金额（与业绩总览/月结同口径）；往下拆到 税务编号/产品 时
  按明细行成本占比分摊，不再使用与实付对不上的吉客云行金额。
- 按仓库汇总：每行一个仓库，输出 月度时间 / 仓库 / 税务编号 / 发货总数量 / 销售总金额 / 销售总成本。
- 仓库取订单原始来源的 warehouseName；税务编号取货品档案，成本只取账期截止前
  采购入库明细的数量加权平均含税单价（与月结/利润中心同口径），不使用货品档案
  default_cost 兜底；个别 SKU 缺入库成本时按已覆盖部分出数，并在 summary 标记
  costIncomplete / costMissingDetail，提示补充采购入库成本后重新生成。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import audit
from app.models.catalog import ProductSku
from app.models.finance import ArchiveFile, FinanceSalesReportTemplate, FinanceUnbilledAdjustment
from app.models.sales import SalesOrder, SalesOrderItem
from app.services import finance_service
from app.services.finance_sales_report_format import (
    DEFAULT_FIELD_KEYS,
    FIELD_REGISTRY,
    DEFAULT_RULES,
    _money,
    _normalize_emails,
    _normalize_fields,
    _order_paid_shares,
    _string_decimal,
    _valid_order_condition,
    _warehouse_of,
    default_fields,
    generated_filename,
    to_xlsx,
    unbilled_detail_key,
)
from app.services.inbound_cost_service import weighted_inbound_costs
from app.services.monthly_core import month_bounds
from app.utils.money import to_decimal

def get_or_create_template(db: Session, company: str = finance_service.DEFAULT_COMPANY) -> FinanceSalesReportTemplate:
    row = (
        db.query(FinanceSalesReportTemplate)
        .filter_by(company=company, name="默认财务月报")
        .first()
    )
    if row is None:
        row = FinanceSalesReportTemplate(
            company=company,
            name="默认财务月报",
            enabled=True,
            fields=default_fields(),
            rules=dict(DEFAULT_RULES),
            to_addrs=[],
            cc_addrs=[],
            auto_send=False,
            send_day=3,
            send_hour=10,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    else:
        # 旧版本模板字段（订单明细口径）不是当前注册字段时，自动升级为仓库汇总默认字段。
        stored_keys = {f.get("key") for f in (row.fields or []) if isinstance(f, dict)}
        if not (stored_keys & set(FIELD_REGISTRY)):
            row.fields = default_fields()
            db.commit()
            db.refresh(row)
    return row


def serialize_template(row: FinanceSalesReportTemplate) -> dict[str, Any]:
    return {
        "id": row.id,
        "company": row.company,
        "name": row.name,
        "enabled": row.enabled,
        "fields": _normalize_fields(row.fields),
        "fieldRegistry": [
            {"key": key, "defaultLabel": meta["label"], "type": meta["type"]}
            for key, meta in FIELD_REGISTRY.items()
        ],
        "rules": {**DEFAULT_RULES, **(row.rules or {})},
        "toAddrs": list(row.to_addrs or []),
        "ccAddrs": list(row.cc_addrs or []),
        "autoSend": row.auto_send,
        "sendDay": row.send_day,
        "sendHour": row.send_hour,
    }


def save_template(
    db: Session,
    *,
    company: str,
    fields: list[dict[str, Any]],
    rules: dict[str, Any] | None,
    to_addrs: list[str] | None,
    cc_addrs: list[str] | None,
    auto_send: bool,
    send_day: int,
    send_hour: int,
    enabled: bool = True,
) -> FinanceSalesReportTemplate:
    if not 1 <= int(send_day) <= 28:
        raise ValueError("自动发送日期只能设置为每月 1-28 日")
    if not 0 <= int(send_hour) <= 23:
        raise ValueError("自动发送小时必须为 0-23")
    row = get_or_create_template(db, company)
    row.fields = _normalize_fields(fields)
    # 当前口径值是受控枚举；前端可调整时仍避免写入未知计算逻辑。
    normalized_rules = dict(DEFAULT_RULES)
    supplied = rules or {}
    if supplied.get("valid_order_mode") in {"paid_or_completed"}:
        normalized_rules["valid_order_mode"] = supplied["valid_order_mode"]
    if supplied.get("refund_mode") in {"recorded_non_cancelled", "ignore_refund"}:
        normalized_rules["refund_mode"] = supplied["refund_mode"]
    normalized_rules["timezone"] = settings.TZ
    normalized_rules["order_level_value_mode"] = "first_item_only"
    row.rules = normalized_rules
    row.to_addrs = _normalize_emails(to_addrs or [])
    row.cc_addrs = _normalize_emails(cc_addrs or [])
    row.auto_send = bool(auto_send)
    row.send_day = int(send_day)
    row.send_hour = int(send_hour)
    row.enabled = bool(enabled)
    db.commit()
    db.refresh(row)
    return row


def build_report(
    db: Session,
    year: int,
    month: int,
    template: FinanceSalesReportTemplate | None = None,
) -> dict[str, Any]:
    if not 1 <= month <= 12:
        raise ValueError("非法月份")
    template = template or get_or_create_template(db)
    rules = {**DEFAULT_RULES, **(template.rules or {})}
    start, nxt = month_bounds(year, month)
    orders = (
        db.query(SalesOrder)
        .filter(_valid_order_condition())
        .filter(SalesOrder.ordered_at >= start, SalesOrder.ordered_at < nxt)
        .order_by(SalesOrder.ordered_at, SalesOrder.id)
        .all()
    )
    order_ids = [row.id for row in orders]
    items_by_order: dict[int, list[SalesOrderItem]] = defaultdict(list)
    sku_codes: set[str] = set()
    if order_ids:
        for item in (
            db.query(SalesOrderItem)
            .filter(SalesOrderItem.order_id.in_(order_ids))
            .order_by(SalesOrderItem.order_id, SalesOrderItem.id)
            .all()
        ):
            items_by_order[item.order_id].append(item)
            if item.sku_code:
                sku_codes.add(item.sku_code)

    sku_meta: dict[str, dict[str, Any]] = {}
    if sku_codes:
        for sku in (
            db.query(ProductSku)
            .filter(ProductSku.sku_code.in_(sku_codes))
            .all()
        ):
            sku_meta[sku.sku_code] = {
                "id": sku.id,
                "name": sku.sku_name or sku.sku_code,
                "tax_code": (sku.tax_code or "").strip(),
            }
    inbound_costs = weighted_inbound_costs(
        db,
        as_of=nxt,
        sku_ids={int(meta["id"]) for meta in sku_meta.values()},
    )

    aggregates: dict[str, dict[str, Any]] = {}
    # 缺入库成本的明细（按 SKU 汇总），用于 summary 告警；不用 default_cost 兜底。
    cost_missing: dict[str, dict[str, Any]] = {}
    for order in orders:
        warehouse = _warehouse_of(order)
        agg = aggregates.setdefault(warehouse, {
            "quantity": Decimal("0"),
            "sales": Decimal("0"),
            "cost": Decimal("0"),
            "tax_codes": set(),
        })
        # 销售金额 = 订单客户实付金额（与业绩总览/月结同口径），订单级只累加一次；
        # 吉客云行金额与实付对不上（含 0 值与负数），不再作为收入基数。
        agg["sales"] += _money(order.paid_amount)
        for item in items_by_order.get(order.id) or []:
            quantity = _money(item.quantity)
            agg["quantity"] += quantity
            if item.sku_code:
                meta = sku_meta.get(item.sku_code)
                if meta:
                    unit_cost = inbound_costs.get(meta["id"])
                    if unit_cost is not None:
                        agg["cost"] += quantity * unit_cost
                    else:
                        missed = cost_missing.setdefault(item.sku_code, {
                            "skuCode": item.sku_code,
                            "skuName": meta["name"],
                            "quantity": Decimal("0"),
                        })
                        missed["quantity"] += quantity
                    if meta["tax_code"]:
                        agg["tax_codes"].add(meta["tax_code"])

    rows: list[dict[str, Any]] = []
    for warehouse, agg in sorted(aggregates.items(), key=lambda kv: kv[1]["sales"], reverse=True):
        rows.append({
            "period": f"{year}-{month:02d}",
            "warehouse": warehouse,
            "tax_code": " / ".join(sorted(agg["tax_codes"])),
            "total_quantity": str(agg["quantity"]),
            "total_sales": _string_decimal(agg["sales"]),
            "total_cost": _string_decimal(agg["cost"]),
        })

    enabled_fields = [f for f in _normalize_fields(template.fields) if f["enabled"]]
    summary = {
        "orderCount": len(orders),
        "warehouseCount": len(rows),
        "totalQuantity": str(sum((Decimal(row["total_quantity"] or "0") for row in rows), Decimal("0"))),
        "salesAmount": _string_decimal(sum((Decimal(row["total_sales"] or "0") for row in rows), Decimal("0"))),
        "costAmount": _string_decimal(sum((Decimal(row["total_cost"] or "0") for row in rows), Decimal("0"))),
        # 缺入库成本时成本只含已覆盖部分；前端据此提示去补充数据后重新生成。
        "costIncomplete": bool(cost_missing),
        "costMissingDetail": [
            {"skuCode": row["skuCode"], "skuName": row["skuName"], "quantity": str(row["quantity"])}
            for row in sorted(cost_missing.values(), key=lambda item: item["skuCode"])
        ],
    }
    return {
        "year": year,
        "month": month,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "template": serialize_template(template),
        "fields": enabled_fields,
        "rules": rules,
        "summary": summary,
        "rows": rows,
    }


def _tax_name_map(db: Session) -> dict[str, str]:
    """税务编号 → 税收分类名称。

    名称取自「税务做账 → 分类规则」中用户维护的 category_name / item_name
    （如 软饮料 · 咖啡），仅用已启用规则，不编造；同一编码取优先级最高的一条。
    """
    from app.models.tax import TaxAccountingCategoryRule

    names: dict[str, str] = {}
    rows = (
        db.query(TaxAccountingCategoryRule)
        .filter(TaxAccountingCategoryRule.enabled.is_(True))
        .order_by(TaxAccountingCategoryRule.priority, TaxAccountingCategoryRule.id)
        .all()
    )
    for row in rows:
        code = (row.tax_code or "").strip()
        if not code or code in names:
            continue
        parts: list[str] = []
        category = (row.category_name or "").strip()
        item = (row.item_name or "").strip()
        if category and category != "全部":
            parts.append(category)
        if item and item != "全部" and item != category:
            parts.append(item)
        if parts:
            names[code] = " · ".join(parts)
    return names


def _order_group_sales(db: Session, order_ids: list[int]) -> dict[int, dict[tuple[str, str], Decimal]]:
    """订单 → (税务编号, 产品) 组销售额；分组口径与 _unbilled_detail_rows 完全一致。

    每组金额 = 订单客户实付金额按明细行成本占比分摊（与总览/月结收入口径一致），
    不再使用与实付对不上的吉客云行金额。
    """
    from app.models.catalog import Product

    result: dict[int, dict[tuple[str, str], Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    if not order_ids:
        return result
    items = (
        db.query(SalesOrderItem)
        .filter(SalesOrderItem.order_id.in_(order_ids))
        .all()
    )
    sku_codes = {item.sku_code for item in items if item.sku_code}
    sku_info: dict[str, dict[str, Any]] = {}
    if sku_codes:
        for sku, goods_name in (
            db.query(ProductSku, Product.goods_name)
            .outerjoin(Product, Product.id == ProductSku.product_id)
            .filter(ProductSku.sku_code.in_(sku_codes))
            .all()
        ):
            sku_info[sku.sku_code] = {
                "id": sku.id,
                "tax_code": (sku.tax_code or "").strip(),
                "product": (sku.sku_name or goods_name or sku.sku_code).strip(),
            }
    inbound_costs = weighted_inbound_costs(
        db,
        as_of=datetime.now(timezone.utc),
        sku_ids={int(info["id"]) for info in sku_info.values()},
    )
    paid_by_order = {
        order_id: _money(paid)
        for order_id, paid in db.query(SalesOrder.id, SalesOrder.paid_amount)
        .filter(SalesOrder.id.in_(order_ids))
        .all()
    }
    rows_by_order: dict[int, list[SalesOrderItem]] = defaultdict(list)
    for item in items:
        rows_by_order[item.order_id].append(item)
    for order_id, order_items in rows_by_order.items():
        weight_rows: list[tuple[SalesOrderItem, Decimal | None]] = []
        for item in order_items:
            info = sku_info.get(item.sku_code or "")
            weight_rows.append((item, inbound_costs.get(int(info["id"])) if info else None))
        shares = _order_paid_shares(paid_by_order.get(order_id, Decimal("0")), weight_rows)
        for item, share in zip(order_items, shares):
            info = sku_info.get(item.sku_code or "")
            tax_code = (info or {}).get("tax_code", "")
            product = (info or {}).get("product") or (item.sku_code or "未匹配货品档案")
            result[order_id][(tax_code, product)] += share
    return result


def _remaining_invoice_amount(total: Decimal, links) -> Decimal:
    """整张发票扣除已显式分摊金额后，剩余可供空分摊关联自动分配的金额。"""
    explicitly_allocated = sum(
        (to_decimal(link.allocated_amount) for link in links if link.allocated_amount is not None),
        Decimal("0"),
    )
    return total - explicitly_allocated


def _output_invoiced_by_group(db: Session, start, nxt) -> tuple[dict[tuple[str, str], Decimal], dict[tuple[str, str], Decimal]]:
    """当月销项发票落到 (税务编号, 产品) 组的已开票金额。

    - 发票口径与 build_unbilled_income_report 完全一致：
      tax_export / direction=output / status in (issued, red)，开票日期在月内；
    - 优先按已确认关联（TaxInvoiceLink.target_type="sales_order", confirmed=True）的
      allocated_amount 归集；同发票未带 allocated_amount 的关联，按所连订单销售额占比
      从发票价税合计分摊（整张发票都无 allocated_amount 时同理）；
    - 订单内再按明细行成本占比把订单实付金额落到 (税务编号, 产品) 组。
    """
    from app.models.tax import TaxInvoice, TaxInvoiceLink

    invoices = (
        db.query(TaxInvoice)
        .filter(
            TaxInvoice.source_system == "tax_export",
            TaxInvoice.direction == "output",
            TaxInvoice.status.in_(("issued", "red")),
            TaxInvoice.issue_date >= start,
            TaxInvoice.issue_date < nxt,
        )
        .all()
    )
    invoice_ids = [row.id for row in invoices]
    if not invoice_ids:
        return {}, {}
    from app.services import tax_invoice_service
    red_context = tax_invoice_service.red_accounting_context(db, invoices)
    related_blue_ids = {
        int(ctx["redRelatedInvoiceId"])
        for ctx in red_context.values()
        if ctx.get("invoiceColor") == "red" and ctx.get("redRelatedInvoiceId")
    }
    link_invoice_ids = sorted(set(invoice_ids) | related_blue_ids)
    links = (
        db.query(TaxInvoiceLink)
        .filter(
            TaxInvoiceLink.invoice_id.in_(link_invoice_ids),
            TaxInvoiceLink.target_type == "sales_order",
            TaxInvoiceLink.confirmed.is_(True),
            TaxInvoiceLink.match_method != "rejected",
        )
        .all()
    )
    links_by_invoice: dict[int, list[TaxInvoiceLink]] = defaultdict(list)
    order_ids: set[int] = set()
    for link in links:
        links_by_invoice[link.invoice_id].append(link)
        order_ids.add(int(link.target_id))
    group_sales_by_order = _order_group_sales(db, sorted(order_ids))
    order_sales = {
        order_id: sum(groups.values(), Decimal("0"))
        for order_id, groups in group_sales_by_order.items()
    }

    attributed: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    red_adjusted: dict[tuple[str, str], Decimal] = defaultdict(Decimal)

    def _attribute(order_id: int, amount: Decimal, *, is_red: bool = False) -> None:
        groups = group_sales_by_order.get(order_id) or {}
        total_sales = order_sales.get(order_id, Decimal("0"))
        if not groups or total_sales <= 0:
            return
        for key, sales in groups.items():
            share = amount * sales / total_sales
            attributed[key] += share
            if is_red:
                red_adjusted[key] += share

    for invoice in invoices:
        red_meta = red_context.get(invoice.id, {})
        invoice_links = links_by_invoice.get(invoice.id) or []
        inherited_from_blue = False
        if not invoice_links and red_meta.get("invoiceColor") == "red" and red_meta.get("redRelatedInvoiceId"):
            invoice_links = links_by_invoice.get(int(red_meta["redRelatedInvoiceId"])) or []
            inherited_from_blue = bool(invoice_links)
        if not invoice_links:
            continue
        total = to_decimal(red_meta.get("accountingNetAmount") or 0)
        if not red_meta.get("accountingNetIncluded", False):
            continue
        # 红字发票继承蓝字原票的销售订单关系，但绝不能继承蓝票的正数 allocated_amount。
        # 红字金额按原销售订单销售额占比分摊，保证折让/退货只冲减原业务，不产生新业务。
        without_alloc = list(invoice_links) if inherited_from_blue else [
            ln for ln in invoice_links if ln.allocated_amount is None
        ]
        remaining = total if inherited_from_blue else _remaining_invoice_amount(total, invoice_links)
        if not inherited_from_blue:
            for link in invoice_links:
                if link.allocated_amount is not None:
                    _attribute(int(link.target_id), to_decimal(link.allocated_amount), is_red=invoice.status == "red")
        if not without_alloc:
            continue
        # 只把剩余未分摊金额分给 allocated_amount 为空的订单，避免重复计算。
        denom = sum(
            (order_sales.get(int(ln.target_id), Decimal("0")) for ln in without_alloc),
            Decimal("0"),
        )
        if denom <= 0:
            continue
        for link in without_alloc:
            sales = order_sales.get(int(link.target_id), Decimal("0"))
            if sales > 0:
                _attribute(int(link.target_id), remaining * sales / denom, is_red=invoice.status == "red")
    return dict(attributed), dict(red_adjusted)


def _unbilled_detail_rows(db: Session, year: int, month: int) -> list[dict[str, Any]]:
    """无票收入明细：按 税务编号 + 产品 聚合当月有效销售（与销售汇总同口径）。

    - 税务编号/产品/成本取自货品档案（ProductSku + Product.goods_name）；
    - 档案缺失的行保留 sku_code，税务编号显示空值，不编造；
    - 销售金额取订单客户实付并按明细成本/数量权重分摊，成本取采购入库加权成本；
    - invoiced = 当月销项发票按已确认关联分摊到该组的已开票金额；
      unbilled = max(sales − invoiced, 0)，超开的负差如实保留为 0。
    """
    from app.models.catalog import Product

    start, nxt = month_bounds(year, month)
    invoiced_by_group, red_adjustment_by_group = _output_invoiced_by_group(db, start, nxt)
    orders = (
        db.query(SalesOrder)
        .filter(_valid_order_condition())
        .filter(SalesOrder.ordered_at >= start, SalesOrder.ordered_at < nxt)
        .all()
    )
    order_ids = [o.id for o in orders]
    items_by_order: dict[int, list[SalesOrderItem]] = defaultdict(list)
    sku_codes: set[str] = set()
    if order_ids:
        for item in (
            db.query(SalesOrderItem)
            .filter(SalesOrderItem.order_id.in_(order_ids))
            .all()
        ):
            items_by_order[item.order_id].append(item)
            if item.sku_code:
                sku_codes.add(item.sku_code)

    sku_info: dict[str, dict[str, Any]] = {}
    if sku_codes:
        for sku, goods_name, goods_category in (
            db.query(ProductSku, Product.goods_name, Product.category)
            .outerjoin(Product, Product.id == ProductSku.product_id)
            .filter(ProductSku.sku_code.in_(sku_codes))
            .all()
        ):
            sku_info[sku.sku_code] = {
                "id": sku.id,
                "tax_code": (sku.tax_code or "").strip(),
                "product": (sku.sku_name or goods_name or sku.sku_code).strip(),
                "category": (goods_category or "").strip(),
            }
    inbound_costs = weighted_inbound_costs(
        db,
        as_of=month_bounds(year, month)[1],
        sku_ids={int(info["id"]) for info in sku_info.values()},
    )

    tax_names = _tax_name_map(db)
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for order in orders:
        order_items = items_by_order.get(order.id) or []
        weight_rows: list[tuple[SalesOrderItem, Decimal | None]] = []
        for item in order_items:
            info = sku_info.get(item.sku_code or "")
            weight_rows.append(
                (item, inbound_costs.get(int(info["id"])) if info else None)
            )
        # 销售额 = 订单客户实付金额按明细行成本占比分摊（与总览/月结口径一致）
        shares = _order_paid_shares(_money(order.paid_amount), weight_rows)
        for item, share in zip(order_items, shares):
            info = sku_info.get(item.sku_code or "")
            tax_code = (info or {}).get("tax_code", "")
            product = (info or {}).get("product") or (item.sku_code or "未匹配货品档案")
            # 税收分类名称：优先分类规则（用户维护），无规则时兜底用货品档案品类。
            tax_name = tax_names.get(tax_code, "") or (info or {}).get("category", "")
            quantity = _money(item.quantity)
            cost = Decimal("0")
            if info:
                info_cost = inbound_costs.get(int(info["id"]))
                if info_cost is not None:
                    cost = quantity * info_cost
            agg = groups.setdefault(
                (tax_code, product),
                {"quantity": Decimal("0"), "sales": Decimal("0"), "cost": Decimal("0"), "tax_name": tax_name},
            )
            agg["quantity"] += quantity
            agg["sales"] += share
            agg["cost"] += cost

    rows: list[dict[str, Any]] = []
    for (tax_code, product), agg in sorted(groups.items(), key=lambda kv: kv[1]["sales"], reverse=True):
        invoiced = invoiced_by_group.get((tax_code, product), Decimal("0"))
        red_adjustment = red_adjustment_by_group.get((tax_code, product), Decimal("0"))
        adjusted_sales = agg["sales"] + red_adjustment
        rows.append({
            "period": f"{year}-{month:02d}",
            "taxCode": tax_code,
            "taxName": agg["tax_name"],
            "product": product,
            "quantity": str(agg["quantity"]),
            "sales": _string_decimal(agg["sales"]),
            "redSalesAdjustment": _string_decimal(red_adjustment),
            "adjustedSales": _string_decimal(adjusted_sales),
            "invoiced": _string_decimal(invoiced),
            "unbilled": _string_decimal(max(adjusted_sales - invoiced, Decimal("0"))),
            "cost": _string_decimal(agg["cost"]),
        })
    return rows


def _latest_unbilled_adjustment(
    db: Session,
    *,
    company: str,
    year: int,
    month: int,
) -> FinanceUnbilledAdjustment | None:
    return (
        db.query(FinanceUnbilledAdjustment)
        .filter_by(company=company, period_year=year, period_month=month)
        .order_by(FinanceUnbilledAdjustment.version.desc())
        .first()
    )


def _unbilled_details_for_period(
    db: Session,
    *,
    company: str,
    year: int,
    month: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], FinanceUnbilledAdjustment | None]:
    """返回原始明细、当前明细和当前选择键；历史版本永远不改写。"""
    source_details = _unbilled_detail_rows(db, year, month)
    adjustment = _latest_unbilled_adjustment(db, company=company, year=year, month=month)
    source_keys = [unbilled_detail_key(row) for row in source_details]
    if adjustment is None:
        return source_details, source_details, source_keys, None

    selected_set = {str(key) for key in (adjustment.selected_keys or [])}
    selected_details = [row for row in source_details if unbilled_detail_key(row) in selected_set]
    current_keys = [unbilled_detail_key(row) for row in selected_details]
    return source_details, selected_details, current_keys, adjustment


def _latest_sales_summary_version(db: Session, *, company: str, year: int, month: int) -> int:
    value = (
        db.query(func.max(ArchiveFile.version))
        .filter_by(company=company, period_year=year, period_month=month, category="sales_summary")
        .scalar()
    )
    return int(value or 0)


def save_unbilled_adjustment(
    db: Session,
    *,
    company: str,
    year: int,
    month: int,
    selected_keys: list[str],
    actor: str = "system",
    note: str = "",
) -> dict[str, Any]:
    """保存本月保留的无票收入明细，并以新版本记录调整。"""
    if not 1 <= month <= 12:
        raise ValueError("非法月份")
    source_details = _unbilled_detail_rows(db, year, month)
    available = {unbilled_detail_key(row) for row in source_details}
    normalized = list(dict.fromkeys(str(key).strip() for key in selected_keys if str(key).strip()))
    unknown = [key for key in normalized if key not in available]
    if unknown:
        raise ValueError("明细已发生变化，请刷新后重新选择")
    if source_details and not normalized:
        raise ValueError("至少保留一条明细")

    previous = (
        db.query(func.max(FinanceUnbilledAdjustment.version))
        .filter_by(company=company, period_year=year, period_month=month)
        .scalar()
    )
    version = max(int(previous or 0), _latest_sales_summary_version(db, company=company, year=year, month=month)) + 1
    row = FinanceUnbilledAdjustment(
        company=company,
        period_year=year,
        period_month=month,
        version=version,
        selected_keys=normalized,
        actor=(actor or "system")[:64],
        note=(note or "")[:500],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    audit(
        db,
        actor,
        "finance.unbilled.adjustment",
        "finance_unbilled_adjustments",
        row.id,
        {"company": company, "year": year, "month": month, "version": version, "selectedCount": len(normalized)},
    )
    return {
        "id": row.id,
        "version": row.version,
        "selectedKeys": normalized,
        "selectedCount": len(normalized),
        "sourceCount": len(source_details),
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def build_unbilled_income_report(
    db: Session,
    year: int,
    month: int,
    company: str = finance_service.DEFAULT_COMPANY,
) -> dict[str, Any]:
    """销售出库无票收入 = 当前销售出库口径 − 销售出库关联的已开票净额。

    - 销售总金额复用 build_report 的 summary.salesAmount（与发给财务的销售汇总同口径）。
    - 已开票金额只取已确认关联到销售订单的销项发票；未关联的服务费等销项发票
      不属于销售出库无票收入表。
    - 人工调整版本按当前勾选的明细计算调整后销售金额、已开票净额和无票收入。
    """
    from app.services.monthly_core import month_bounds

    sales = build_report(db, year, month, get_or_create_template(db, company))
    start, nxt = month_bounds(year, month)
    invoiced_by_group, red_adjustment_by_group = _output_invoiced_by_group(db, start, nxt)
    linked_invoiced_total = sum(invoiced_by_group.values(), Decimal("0"))
    linked_red_adjustment = sum(red_adjustment_by_group.values(), Decimal("0"))
    sales_total = Decimal(sales["summary"]["salesAmount"] or "0")
    source_details, details, selected_keys, adjustment = _unbilled_details_for_period(
        db, company=company, year=year, month=month,
    )
    row_invoiced_total = sum((Decimal(d.get("invoiced") or "0") for d in details), Decimal("0"))
    if adjustment is None:
        red_sales_adjustment = linked_red_adjustment
        adjusted_sales_total = sales_total + red_sales_adjustment
        invoiced_total = linked_invoiced_total
    else:
        # 当前版本只交付勾选的销售出库明细，汇总也必须跟随该版本，不能把取消的行算回去。
        red_sales_adjustment = sum((Decimal(d.get("redSalesAdjustment") or "0") for d in details), Decimal("0"))
        adjusted_sales_total = sum(
            (Decimal(d.get("adjustedSales") or d.get("sales") or "0") for d in details),
            Decimal("0"),
        )
        invoiced_total = row_invoiced_total
    # 红字发票同时冲减销售基数与已开票净额；未关联的服务费发票不参与本表。
    unbilled = adjusted_sales_total - invoiced_total
    version = adjustment.version if adjustment is not None else _latest_sales_summary_version(
        db, company=company, year=year, month=month,
    )
    unattributed = invoiced_total - row_invoiced_total
    if unattributed < 0:
        unattributed = Decimal("0")
    return {
        "period": f"{year}-{month:02d}",
        "year": year,
        "month": month,
        "salesAmount": _string_decimal(sales_total),
        "redSalesAdjustmentAmount": _string_decimal(red_sales_adjustment),
        "adjustedSalesAmount": _string_decimal(adjusted_sales_total),
        "invoicedAmount": _string_decimal(invoiced_total),
        "unbilledAmount": _string_decimal(unbilled),
        "rowInvoicedTotal": _string_decimal(row_invoiced_total),
        "unattributedInvoiced": _string_decimal(unattributed),
        "version": version or None,
        "adjusted": adjustment is not None,
        "selectedKeys": selected_keys,
        "sourceCount": len(source_details),
        "selectedCount": len(details),
        "updatedAt": adjustment.updated_at.isoformat() if adjustment and adjustment.updated_at else None,
        # 调整弹窗必须能看到完整原始明细，才能把上一版本取消的行重新勾回来；
        # details 始终代表当前保存版本，用于只读预览和最终交付。
        "sourceDetails": source_details,
        "details": details,
    }


def _unbilled_value(detail: dict[str, Any], key: str, fallback: str = "0") -> Decimal:
    value = detail.get(key)
    if value in (None, ""):
        value = fallback
    return to_decimal(value)


def _unbilled_summary_details(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按税务编号 + 税收分类名称聚合，生成汇总页使用的行。"""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for detail in details:
        key = (str(detail.get("taxCode") or ""), str(detail.get("taxName") or ""))
        row = grouped.setdefault(
            key,
            {
                "period": detail.get("period", ""),
                "taxCode": key[0],
                "taxName": key[1],
                "quantity": Decimal("0"),
                "sales": Decimal("0"),
                "redSalesAdjustment": Decimal("0"),
                "adjustedSales": Decimal("0"),
                "invoiced": Decimal("0"),
                "unbilled": Decimal("0"),
                "cost": Decimal("0"),
            },
        )
        row["quantity"] += _unbilled_value(detail, "quantity")
        row["sales"] += _unbilled_value(detail, "sales")
        row["redSalesAdjustment"] += _unbilled_value(detail, "redSalesAdjustment")
        row["adjustedSales"] += _unbilled_value(detail, "adjustedSales", detail.get("sales") or "0")
        row["invoiced"] += _unbilled_value(detail, "invoiced")
        row["unbilled"] += _unbilled_value(detail, "unbilled")
        row["cost"] += _unbilled_value(detail, "cost")
    return sorted(grouped.values(), key=lambda row: row["sales"], reverse=True)


def _write_unbilled_sheet(
    ws: Any,
    report: dict[str, Any],
    details: list[dict[str, Any]],
    *,
    include_product: bool,
) -> None:
    head = Font(bold=True, size=13)
    bold = Font(bold=True)
    gray = Font(size=9, color="999999")

    ws["A1"] = f"{report['year']}年{report['month']:02d}月销售出库-无票收入"
    ws["A1"].font = head
    adjusted_label = "调整后销售金额" if include_product else "红冲后-销售金额"
    summary_rows = [
        ("月度时间", report["period"], None),
        ("销售总金额", float(report["salesAmount"]), "0.00"),
        ("销项红字调整", float(report.get("redSalesAdjustmentAmount", 0)), "0.00"),
        (adjusted_label, float(report.get("adjustedSalesAmount", report["salesAmount"])), "0.00"),
        ("已开票净额", float(report["invoicedAmount"]), "0.00"),
        ("无票收入", float(report["unbilledAmount"]), "0.00"),
    ]
    for label, value, fmt in summary_rows:
        ws.append([label, value])
        ws.cell(row=ws.max_row, column=1).font = bold
        if fmt:
            ws.cell(row=ws.max_row, column=2).number_format = fmt
    ws.append(["口径：销项红字同时冲减销售基数与已开票净额；无票收入 = 调整后销售金额 − 已开票净额，红字折让/退货不会变成无票收入"])
    ws.cell(row=ws.max_row, column=1).font = gray
    ws.append([])

    if include_product:
        headers = ["月度时间", "税务编号", "税收分类名称", "产品", "发货数量", "销售金额", "销项红字调整", "调整后销售金额", "已开票金额", "无票收入", "销售成本"]
    else:
        headers = ["月度时间", "税务编号", "税收分类名称", "发货数量", "销售金额", "销项红字调整", "红冲后-销售金额", "已开票金额", "无票收入", "销售成本"]
    ws.append(headers)
    header_row = ws.max_row
    for cell in ws[header_row]:
        cell.font = bold

    totals = {"quantity": Decimal("0"), "sales": Decimal("0"), "red": Decimal("0"), "adjusted": Decimal("0"), "invoiced": Decimal("0"), "unbilled": Decimal("0"), "cost": Decimal("0")}
    for detail in details:
        quantity = _unbilled_value(detail, "quantity")
        sales = _unbilled_value(detail, "sales")
        red_adjustment = _unbilled_value(detail, "redSalesAdjustment")
        adjusted_sales = _unbilled_value(detail, "adjustedSales", detail.get("sales") or "0")
        invoiced = _unbilled_value(detail, "invoiced")
        unbilled = _unbilled_value(detail, "unbilled")
        cost = _unbilled_value(detail, "cost")
        totals["quantity"] += quantity
        totals["sales"] += sales
        totals["red"] += red_adjustment
        totals["adjusted"] += adjusted_sales
        totals["invoiced"] += invoiced
        totals["unbilled"] += unbilled
        totals["cost"] += cost
        if include_product:
            ws.append([detail["period"], detail["taxCode"], detail.get("taxName", ""), detail["product"], float(quantity), float(sales), float(red_adjustment), float(adjusted_sales), float(invoiced), float(unbilled), float(cost)])
        else:
            ws.append([detail["period"], detail["taxCode"], detail.get("taxName", ""), float(quantity), float(sales), float(red_adjustment), float(adjusted_sales), float(invoiced), float(unbilled), float(cost)])

    total_label_column = 4 if include_product else 3
    total_row = [""] * len(headers)
    total_row[total_label_column - 1] = "合计"
    numeric_start = total_label_column + 1
    total_values = [totals["quantity"], totals["sales"], totals["red"], totals["adjusted"], totals["invoiced"], totals["unbilled"], totals["cost"]]
    total_row[numeric_start - 1:] = [float(value) for value in total_values]
    ws.append(total_row)
    for cell in ws[ws.max_row]:
        cell.font = bold
    for row_idx in range(header_row, ws.max_row + 1):
        ws.cell(row=row_idx, column=2).number_format = "@"
    for col in range(numeric_start, len(headers) + 1):
        for row_idx in range(header_row, ws.max_row + 1):
            ws.cell(row=row_idx, column=col).number_format = "0.00" if col != numeric_start else "0.####"

    widths = (14, 26, 18, 34, 12, 14, 14, 18, 14, 14, 14) if include_product else (14, 26, 18, 12, 14, 14, 18, 14, 14, 14)
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.freeze_panes = f"A{header_row + 1}"


def unbilled_income_xlsx(report: dict[str, Any]) -> bytes:
    """按财务交付模板生成「无票收入-汇总」和「无票收入-明细」两个页签。"""
    wb = Workbook()
    summary_ws = wb.active
    summary_ws.title = "无票收入-汇总"
    detail_ws = wb.create_sheet("无票收入-明细")
    details = report.get("details") or []
    _write_unbilled_sheet(
        summary_ws,
        report,
        _unbilled_summary_details(details),
        include_product=False,
    )
    _write_unbilled_sheet(detail_ws, report, details, include_product=True)

    output = BytesIO()
    wb.save(output)
    return output.getvalue()


def has_generated_report(db: Session, company: str, year: int, month: int) -> bool:
    return (
        db.query(ArchiveFile.id)
        .filter_by(
            company=company,
            period_year=year,
            period_month=month,
            category="sales_summary",
            original_name=generated_filename(year, month),
        )
        .first()
        is not None
    )


def generate_and_archive(
    db: Session,
    *,
    company: str,
    year: int,
    month: int,
    actor: str = "system",
    skip_if_exists: bool = False,
) -> dict[str, Any]:
    template = get_or_create_template(db, company)
    if not template.enabled:
        return {"status": "skipped", "reason": "template_disabled"}
    if skip_if_exists and has_generated_report(db, company, year, month):
        return {"status": "exists", "period": f"{year}-{month:02d}"}
    report = build_report(db, year, month, template)
    content = to_xlsx(report)
    archive = finance_service.store_upload(
        db,
        company=company,
        year=year,
        month=month,
        category="sales_summary",
        original_name=generated_filename(year, month),
        content=content,
        actor=actor,
    )
    return {
        "status": "ok",
        "period": f"{year}-{month:02d}",
        "archiveFileId": archive.id,
        "version": archive.version,
        "summary": report["summary"],
    }
