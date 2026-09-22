"""基于现有采购事实生成“生产采购”归档视图。

设计原则：
- ExternalPurchaseOrder / 1688 订单仍是来源主单，不复制采购事实；
- 正品(goods)采购按采购状态自动归入生产 / 在途 / 到货 / 完成；
- 耗材(consumable)采购从生产视图排除，继续走耗材采购链；
- 独立 ProductionOrder 只承载手工/内部生产执行，不强行迁移历史数据。

该模块只读，不修改业务数据，因此旧数据无需迁移即可立即归位显示。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.services.procurement_chain_service import ChainPrefetch, _order_row, _source_pairs
from app.services.procurement_workbench_service import _consumable_source_nos, _order_kind, _step_states


_STAGE_META: dict[str, tuple[str, str, str]] = {
    "pending_refine": ("pending", "待确认", "production"),
    "confirmed": ("waiting", "待生产", "production"),
    "jackyun_linked": ("waiting", "待生产", "production"),
    "producing": ("producing", "生产中", "production"),
    "shipped": ("shipped", "已发货", "transit"),
    "arrived": ("arrived", "已到货", "receiving"),
    "inbound": ("inbound", "已入库", "receiving"),
    "done": ("done", "完成", "archive"),
}
_VALID_GROUPS = {"all", "production", "transit", "receiving", "archive"}
_VALID_STAGES = {meta[0] for meta in _STAGE_META.values()}


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _items(row: dict[str, Any]) -> list[dict[str, Any]]:
    """优先使用已经确认/匹配的采购 SKU；没有时回退 1688 原始明细。"""
    allocations = row.get("allocations") or []
    if allocations:
        return [
            {
                "skuId": item.get("skuId"),
                "skuCode": item.get("skuCode") or "",
                "name": item.get("goodsName") or item.get("skuCode") or "未命名货品",
                "spec": "",
                "quantity": _number(item.get("quantity")),
                "amount": _number(item.get("amount")),
                "source": "allocation",
            }
            for item in allocations
        ]
    return [
        {
            "skuId": item.get("skuId"),
            "skuCode": item.get("productNumber") or "",
            "name": item.get("productName") or item.get("productNumber") or "1688商品",
            "spec": item.get("spec") or "",
            "quantity": _number(item.get("quantity")),
            "amount": _number(item.get("amount")),
            "source": "1688",
        }
        for item in (row.get("orderItems") or [])
    ]


def _effective_purchase_status(row: dict[str, Any]) -> str:
    status = str(row.get("purchaseStatus") or "").strip()
    # 终态不能被入库事实降级；采购工作台的收尾完成口径在这里保持一致。
    if status == "done" or _step_states(row).get("closeout", {}).get("done"):
        return "done"
    # 真实吉客云入库关联优先于旧采购状态，避免已入库订单仍停留在待生产。
    if row.get("inbound"):
        return "inbound"
    if status in _STAGE_META:
        return status
    if row.get("purchaseContentComplete"):
        return "confirmed"
    return "pending_refine"


def list_production_purchase_rows(
    db: Session,
    *,
    group: str = "all",
    stage: str = "",
    q: str = "",
    limit: int = 1000,
) -> dict[str, Any]:
    """把现有采购主单按业务阶段归入生产相关页面。"""
    group = group if group in _VALID_GROUPS else "all"
    stage_filter = stage if stage in _VALID_STAGES else ""
    term = q.strip().lower()
    pf = ChainPrefetch(db)
    consumable_nos = _consumable_source_nos(db)
    rows: list[dict[str, Any]] = []

    for source_order, external in _source_pairs(db):
        aggregate = _order_row(db, source_order, external, pf=pf)
        kind = _order_kind(
            aggregate.get("orderNo"),
            aggregate.get("supplier"),
            consumable_nos,
            aggregate.get("orderKindOverride", ""),
            bool(aggregate.get("consumable")),
        )
        if kind != "goods":
            continue

        purchase_status = _effective_purchase_status(aggregate)
        stage, stage_label, archive_group = _STAGE_META[purchase_status]
        if group != "all" and archive_group != group:
            continue
        if stage_filter and stage != stage_filter:
            continue

        items = _items(aggregate)
        sku_text = " ".join(
            f"{item.get('skuCode', '')} {item.get('name', '')} {item.get('spec', '')}"
            for item in items
        )
        haystack = (
            f"{aggregate.get('orderNo', '')} {aggregate.get('supplier', '')} "
            f"{aggregate.get('title', '')} {sku_text}"
        ).lower()
        if term and term not in haystack:
            continue

        inbound = aggregate.get("inbound") or []
        purchase_orders = aggregate.get("purchaseOrders") or []
        logistics = (external.logistics or {}) if external is not None else {}
        workbench_order_id = (
            source_order.id if source_order is not None
            else (-external.id if external is not None else None)
        )
        quantity_total = sum(item.get("quantity") or 0 for item in items)

        rows.append({
            "orderId": workbench_order_id,
            "externalPoId": aggregate.get("externalPoId"),
            "orderNo": aggregate.get("orderNo") or "",
            "platform": aggregate.get("platform") or "other",
            "orderKind": "goods",
            "supplier": aggregate.get("supplier") or "",
            "title": aggregate.get("title") or "",
            "orderDate": aggregate.get("orderDate"),
            "amount": aggregate.get("amount"),
            "purchaseStatus": purchase_status,
            "stage": stage,
            "stageLabel": stage_label,
            "archiveGroup": archive_group,
            "items": items,
            "itemCount": len(items),
            "quantityTotal": quantity_total,
            "jackyunPurchaseNos": [item.get("purchNo") for item in purchase_orders if item.get("purchNo")],
            "jackyunInboundNos": [item.get("goodsdocNo") for item in inbound if item.get("goodsdocNo")],
            "inboundWarehouses": sorted({item.get("warehouseName") for item in inbound if item.get("warehouseName")}),
            "shipStatus": (external.ship_status or "") if external is not None else "",
            "logistics": logistics,
            "hasException": bool(aggregate.get("pendingCount")),
            "source": "purchase",
        })

    rows.sort(key=lambda item: (item.get("orderDate") or "", item.get("orderNo") or ""), reverse=True)
    rows = rows[:limit]
    summary = {
        "count": len(rows),
        "productionCount": sum(1 for row in rows if row["archiveGroup"] == "production"),
        "producingCount": sum(1 for row in rows if row["stage"] == "producing"),
        "transitCount": sum(1 for row in rows if row["archiveGroup"] == "transit"),
        "receivingCount": sum(1 for row in rows if row["archiveGroup"] == "receiving"),
        "completedCount": sum(1 for row in rows if row["archiveGroup"] == "archive"),
    }
    return {"rows": rows, "summary": summary, "group": group, "stage": stage_filter}
