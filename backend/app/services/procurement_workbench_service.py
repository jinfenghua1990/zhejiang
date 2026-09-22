"""采购执行中心（5 步骤任务视角）服务。

与 7 环节链路（追踪视角）共用 ChainPrefetch 与 _order_row，避免维护两套事实。
- 5 步 ① 包含 7 环节 ①
- 5 步 ② = 7 环节 ② 的一部分（purchaseContentComplete）
- 5 步 ③ 是 7 环节 ② 的细分（SKU 关联到吉客云）
- 5 步 ④ = 7 环节 ③
- 5 步 ⑤ = 7 环节 ④⑤⑥⑦ 的并集
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import audit
from app.models.alibaba1688_import import Alibaba1688Order
from app.models.catalog import Warehouse
from app.models.consumable import Consumable
from app.models.ops import ExceptionRecord
from app.models.purchase import ExternalPurchaseOrder
from app.services.procurement_chain_service import (
    _order_row,
    _source_pairs,
    chain_snapshot,
    order_supplier_history,
    supplier_detail,
    supplier_summaries,
)
from app.services.purchase_service import _UNSET, _validate_target_warehouse, update_external_po
from app.utils.money import to_decimal

# 订单类型：正品 / 耗材（外包装等包材采购）。
# 耗材采购的 1688 订单与正常货品订单同列表管理，仅以标签区分，便于采购员识别跟进。
# 判定口径：① 1688 订单号出现在耗材历史档案的「采购订货号」；② 供应商名含包装/印刷等关键词。
_CONSUMABLE_SELLER_KEYWORDS = ("包装", "印刷", "印务", "耗材", "包材")


def _consumable_source_nos(db: Session) -> set[str]:
    """耗材档案（consumables.raw.row.采购订货号）引用过的采购订单号集合。"""
    nos: set[str] = set()
    for (raw,) in db.query(Consumable.raw).all():
        if not raw or not isinstance(raw, dict):
            continue
        no = (raw.get("row") or {}).get("采购订货号")
        if no:
            nos.add(str(no).strip())
    return nos


def _order_kind(order_no: str | None, supplier: str | None, consumable_nos: set[str],
                override: str = "", has_consumable_hc: bool = False) -> str:
    """返回订单类型：consumable=耗材（包材）采购 / goods=正常货品。

    人工覆盖优先（耗材档案 Excel 的「采购订货号」可能填错，自动判定仅作默认）。
    判定优先级：人工覆盖 > 挂有非取消耗材 HC 单 > 耗材档案采购订货号 > 供应商关键词 > 正品。
    挂了 HC 单是平台上最直接的「货品类型」信号（2026-09-07：手工录入单默认正品且不更新，
    用户要求按货品类型自动带出）。
    """
    if override in ("goods", "consumable"):
        return override
    if has_consumable_hc:
        return "consumable"
    if order_no and order_no.strip() in consumable_nos:
        return "consumable"
    name = supplier or ""
    return "consumable" if any(k in name for k in _CONSUMABLE_SELLER_KEYWORDS) else "goods"


def _number(value: Any) -> float | None:
    """把订单明细中的 Decimal / 字符串安全转成前端可用数字。"""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _view_decimal(value: Any) -> Decimal:
    """把链路序列化视图值恢复为 Decimal；仅用于内部 view-dict → 业务计算边界。

    _order_row 为兼容前端会输出 JSON 数字（float），这里立即通过其十进制文本表示
    恢复 Decimal。全局 money.to_decimal 继续严格拒绝 float，避免业务层误用二进制金额。
    """
    if isinstance(value, float):
        return Decimal(str(value))
    return to_decimal(value)


def _channel_key(platform: str | None) -> str:
    value = (platform or "").strip().lower()
    if value == "1688" or "阿里" in value:
        return "1688"
    if "pdd" in value or "拼多多" in value or "duoduo" in value:
        return "pdd"
    if "taobao" in value or "淘宝" in value or "tmall" in value or "天猫" in value:
        return "taobao"
    return "other"


def _logistics_value(logistics: dict[str, Any], *keys: str) -> str:
    """兼容不同渠道的物流 JSON 字段，缺失时保持为空而不伪造物流事实。"""
    for key in keys:
        value = logistics.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _supply_chain_list_fields(row: dict[str, Any]) -> dict[str, Any]:
    """把采购主单折叠成供应链工作台表格所需的跨维度字段。

    优先使用已确认的 SKU 分配，其次才回退到 1688 原始商品行；仓库优先读取已关联的
    真实入库单，尚未入库时回退到采购订单的计划仓库。这样列表既能提前显示去向，
    又不会把计划仓库冒充成实际入库事实。
    """
    allocations = row.get("allocations") or []
    consumable = row.get("consumable") or {}
    order_items = row.get("orderItems") or []
    if allocations:
        item_rows = allocations
        product_name = str(item_rows[0].get("goodsName") or item_rows[0].get("skuCode") or "")
        product_unit = ""
    elif consumable.get("items"):
        item_rows = consumable.get("items") or []
        product_name = str(item_rows[0].get("name") or item_rows[0].get("code") or "")
        product_unit = str(item_rows[0].get("unit") or "")
    else:
        item_rows = order_items
        product_name = str(item_rows[0].get("productName") or item_rows[0].get("productNumber") or row.get("title") or "") if item_rows else str(row.get("title") or "")
        product_unit = ""

    quantity_total = sum(_number(item.get("quantity")) or 0 for item in item_rows)
    if len(item_rows) > 1 and product_name:
        product_name = f"{product_name} 等 {len(item_rows)} 款"

    inbound = row.get("inbound") or []
    primary_inbound = inbound[0] if inbound else {}
    warehouse_name = str(
        primary_inbound.get("warehouseName")
        or consumable.get("warehouseName")
        or (consumable.get("location") if consumable.get("location") not in {"own", "factory"} else "")
        or row.get("targetWarehouseName")
        or ""
    )
    inbound_numbers: list[str] = []
    for inbound_row in inbound:
        number = str(inbound_row.get("goodsdocNo") or "").strip()
        if number and number not in inbound_numbers:
            inbound_numbers.append(number)
    inbound_no = "、".join(inbound_numbers) or str(consumable.get("receiptNo") or "")
    logistics = row.get("logistics") or {}
    if not isinstance(logistics, dict):
        logistics = {}
    return {
        "productName": product_name,
        "productQuantity": quantity_total or None,
        "productUnit": product_unit,
        "itemCount": len(item_rows),
        "warehouseName": warehouse_name,
        "warehouseId": row.get("targetWarehouseId"),
        "logisticsNo": _logistics_value(logistics, "trackingNo", "tracking_no", "logisticsNo", "logistics_no", "waybillNo", "waybill_no", "logisticNo", "logistic_no"),
        "logisticsCompany": _logistics_value(logistics, "company", "logisticsCompany", "logistics_company", "logisticName", "logistic_name", "carrier"),
        "expectedArrival": _logistics_value(logistics, "expectedArrival", "expected_arrival", "eta", "estimatedArrival"),
        "shipAt": _logistics_value(logistics, "shipAt", "ship_at", "shippedAt", "shipped_at", "shippingTime"),
        "jackyunInboundNo": inbound_no,
    }


def edit_order_main_fields(
    db: Session,
    *,
    order_id: int,
    actor: str = "",
    supplier_name: str | None = None,
    title: str | None = None,
    ordered_at=None,
    goods_total=None,
    freight=None,
    discount=None,
    actual_payment=None,
    order_amount=None,
    paid_amount=None,
    platform: str | None = None,
    external_order_id: str | None = None,
    warehouse_id: int | None | object = _UNSET,
) -> dict:
    """网页交互编辑订单主档（供应商/标题/金额等），全部留审计。

    - 1688 导入订单（order_id>0）：改的是源单事实字段（供应商 / 商品金额 / 运费 /
      优惠 / 实付），并按导入口径同步工作流副本：
      ``order_amount = goods + freight - discount``、``paid_amount = 实付``。
    - 工作流独有订单（order_id<0，无 1688 原件）：直接更新副本主档字段
      （复用采购中心 update_external_po，自带校验与审计）。该类本地采购单的
      订单号就是系统主单号，允许在这里修正。
    """
    order: Alibaba1688Order | None = None
    external: ExternalPurchaseOrder | None = None
    for candidate_order, candidate_external in _source_pairs(db):
        candidate_id = candidate_order.id if candidate_order is not None else -candidate_external.id
        if candidate_id == order_id:
            order, external = candidate_order, candidate_external
            break
    if order is None and external is None:
        raise ValueError("订单不存在或已被删除")

    if order is None:
        if external_order_id is None:
            order_no = None
        elif not external_order_id.strip():
            raise ValueError("采购订单号不能为空")
        else:
            order_no = external_order_id
        update_external_po(
            db, external,  # type: ignore[arg-type]
            external_order_id=order_no,
            platform=platform, supplier_name=supplier_name, title=title, ordered_at=ordered_at,
            order_amount=order_amount, paid_amount=paid_amount, warehouse_id=warehouse_id, actor=actor,
        )
        return {"ok": True, "mode": "external", "orderNo": external.external_order_id}  # type: ignore[union-attr]

    if external_order_id is not None and external_order_id.strip() != (order.external_order_id or "").strip():
        raise ValueError("1688 原始订单号不能修改，请以原始平台订单号为准")

    def _money(value, label: str, *, non_negative: bool = True) -> Decimal:
        parsed = to_decimal(value)
        if parsed is None or not parsed.is_finite():
            raise ValueError(f"{label}必须是有效数字")
        if non_negative and parsed < 0:
            raise ValueError(f"{label}不能为负数")
        return parsed.quantize(Decimal("0.01"))

    before: dict[str, object] = {}
    after: dict[str, object] = {}
    target_warehouse = None
    if warehouse_id is not _UNSET:
        target_warehouse = _validate_target_warehouse(db, warehouse_id)  # type: ignore[arg-type]
        before["warehouseId"] = order.warehouse_id
        after["warehouseId"] = target_warehouse.id if target_warehouse is not None else None
        order.warehouse_id = target_warehouse.id if target_warehouse is not None else None
    if supplier_name is not None:
        name = supplier_name.strip()
        if not name:
            raise ValueError("供应商不能为空")
        before["supplier"] = order.seller_company_name
        order.seller_company_name = name
        after["supplier"] = name
    money_changed = False
    if goods_total is not None:
        value = _money(goods_total, "商品金额")
        before["goodsTotal"] = str(order.goods_total)
        order.goods_total = value
        after["goodsTotal"] = str(value)
        money_changed = True
    if freight is not None:
        value = _money(freight, "运费")
        before["freight"] = str(order.freight)
        order.freight = value
        after["freight"] = str(value)
        money_changed = True
    if discount is not None:
        value = _money(discount, "优惠/调整", non_negative=False)
        before["discount"] = str(order.discount)
        order.discount = value
        after["discount"] = str(value)
        money_changed = True
    if actual_payment is not None:
        value = _money(actual_payment, "实付金额")
        before["paidAmount"] = str(order.actual_payment)
        order.actual_payment = value
        after["paidAmount"] = str(value)
        money_changed = True
    if title is not None and external is not None:
        before["title"] = external.title
        external.title = title.strip()
        after["title"] = external.title
    if external is not None:
        if warehouse_id is not _UNSET:
            external.warehouse_id = target_warehouse.id if target_warehouse is not None else None
        if money_changed:
            before.setdefault("copyOrderAmount", str(external.order_amount))
            before.setdefault("copyPaidAmount", str(external.paid_amount))
            external.order_amount = (order.goods_total or Decimal("0")) + (order.freight or Decimal("0")) - (order.discount or Decimal("0"))
            external.paid_amount = order.actual_payment
            after["copyOrderAmount"] = str(external.order_amount)
            after["copyPaidAmount"] = str(external.paid_amount)
        if "supplier" in after:
            external.supplier_name = order.seller_company_name
    audit(db, actor, "procurement.workbench.order_edit_main", "alibaba1688_orders", order.id,
          {"orderNo": order.external_order_id, "before": before, "after": after})
    return {"ok": True, "mode": "file", "orderNo": order.external_order_id}


def rename_supplier(db: Session, *, old_name: str, new_name: str, actor: str = "") -> dict:
    """供应商改名/归一：把该供应商全部订单的供应商写法统一改为新名称。

    - 1688 源单副本 ``seller_company_name`` 与工作流副本 ``supplier_name``
      按旧名精确匹配后统一改写（与单笔订单改名 edit_order_main_fields 同口径，
      供应商聚合视图优先取源单卖家名，两边都要改才生效）；
    - 新名称若与现有供应商相同即合并（聚合视图按名称实时归组）；
    - 发票 ``seller_name`` 是税务事实不改名；订单名归一后发票按名称匹配的
      命中率自然提升；
    - 全程写审计。
    """
    old_name = (old_name or "").strip()
    new_name = (new_name or "").strip()
    if not old_name:
        raise ValueError("原供应商名称不能为空")
    if not new_name:
        raise ValueError("新供应商名称不能为空")
    if old_name == new_name:
        raise ValueError("新名称与当前名称相同")

    po_rows = (
        db.query(ExternalPurchaseOrder)
        .filter(ExternalPurchaseOrder.supplier_name == old_name)
        .all()
    )
    file_rows = (
        db.query(Alibaba1688Order)
        .filter(Alibaba1688Order.seller_company_name == old_name)
        .all()
    )
    if not po_rows and not file_rows:
        raise ValueError(f"未找到名称为「{old_name}」的供应商订单")

    merged = (
        db.query(ExternalPurchaseOrder.id)
        .filter(ExternalPurchaseOrder.supplier_name == new_name)
        .count()
    ) > 0

    for po in po_rows:
        po.supplier_name = new_name
    for fo in file_rows:
        fo.seller_company_name = new_name

    audit(db, actor, "procurement.workbench.supplier_rename", "supplier", old_name,
          {"oldName": old_name, "newName": new_name, "merged": merged,
           "renamedOrders": len(po_rows), "renamedFileOrders": len(file_rows),
           "poIds": [po.id for po in po_rows], "fileOrderIds": [fo.id for fo in file_rows]})
    db.commit()
    return {
        "ok": True, "oldName": old_name, "newName": new_name,
        "renamedOrders": len(po_rows), "renamedFileOrders": len(file_rows), "merged": merged,
    }

# 5 步骤：采购员日常推进的执行漏斗
# 顺序即采购员在系统里的推进路径；吉客云采购单只作为历史参考，不再阻塞本系统入库。
WORKBENCH_STEPS: list[dict] = [
    {"key": "order",      "no": 1, "label": "1688 已下单",    "short": "1688",    "dimension": "1688",     "href": "/alibaba1688-import", "act": "导入 1688 订单"},
    {"key": "content",    "no": 2, "label": "确认采购内容",    "short": "采购内容", "dimension": "purchase", "href": "/purchase",          "act": "去采购中心细化"},
    {"key": "sku",        "no": 3, "label": "选择吉客云货品",  "short": "选货品",   "dimension": "purchase", "href": "/purchase",          "act": "关联吉客云 SKU"},
    {"key": "jackyun_po", "no": 4, "label": "本系统采购单",    "short": "采购单",   "dimension": "purchase", "href": "/purchase",          "act": "查看本系统采购单"},
    {"key": "closeout",   "no": 5, "label": "入库/发票/付款",  "short": "收尾",     "dimension": "purchase", "href": "/purchase",          "act": "查看入库/发票/付款"},
]
WORKBENCH_TOTAL = len(WORKBENCH_STEPS)


def _step_states(row: dict) -> dict[str, dict]:
    """5 步骤每步的完成状态 + 简短摘要（与 7 环节口径共享 row 字段）。"""
    allocations = row.get("allocations") or []
    sku_linked = [a for a in allocations if a.get("skuId")]
    inbound = row.get("inbound") or []
    order_items = row.get("orderItems") or []
    order_item_codes = [i.get("productNumber") for i in order_items if i.get("productNumber")]
    consumable = row.get("consumable") or {}
    consumable_received = bool(consumable.get("received"))
    consumable_items = consumable.get("items") or []
    consumable_codes = [i.get("code") for i in consumable_items if i.get("code")]
    # 耗材单（本平台）：录入 HC 明细即内容已确认，且无需吉客云货品/采购单。
    is_consumable = consumable_received or bool(consumable_items)

    if consumable_items and not consumable_received:
        content_detail = f"耗材已录入 {len(consumable_items)} 项"
        if consumable_codes:
            content_detail += "：" + "、".join(consumable_codes)
    elif row.get("purchaseContentComplete"):
        content_detail = "已细化"
    elif consumable_received:
        content_detail = f"耗材已收货 {len(consumable_items)} 项"
        if consumable_codes:
            content_detail += "：" + "、".join(consumable_codes)
    elif allocations:
        content_detail = "未细化实际采购内容"
    elif order_items:
        content_detail = f"待细化 {len(order_items)} 项（1688 明细）"
        if order_item_codes:
            content_detail += "：" + "、".join(order_item_codes)
    else:
        content_detail = "未细化实际采购内容"
    content_amount = (
        sum(a.get("amount") or 0 for a in allocations)
        or sum(i.get("amount") or 0 for i in order_items)
        or None
    )
    # 入库状态只由真实入库事实决定。耗材映射/自动扣减是入库后的独立处理结果，
    # 不能因为映射尚未维护就把已经有入库单的订单继续显示为“待入库”。
    inbound_ready = bool(inbound) or consumable_received
    invoice = row.get("invoice") or []
    settlement = row.get("settlement") or []
    try:
        reported_paid = Decimal(str(row.get("paidAmount"))) > 0 if row.get("paidAmount") is not None else False
    except (TypeError, ValueError):
        reported_paid = False
    paid_any = any(s.get("paid") for s in settlement) or bool(row.get("paidOn1688")) or reported_paid
    invoice_status = row.get("invoiceStatus") or "none"
    invoice_done = invoice_status == "done"
    invoice_outstanding = row.get("invoiceOutstanding")
    if invoice_status == "pending":
        invoice_text = "待供应商开票"
    elif invoice_status == "partial":
        invoice_text = f"待开票 ¥{invoice_outstanding:,.2f}" if invoice_outstanding else "部分开票"
    elif invoice_done:
        invoice_text = f"已开票 ¥{row.get('invoicedAmount') or 0:,.2f}"
    else:
        invoice_text = "待收票"

    return {
        "order": {
            "done": True,
            "detail": row.get("orderNo") or "—",
            "amount": row.get("amount"),
        },
        "content": {
            "done": bool(row.get("purchaseContentComplete")) or consumable_received,
            "detail": content_detail,
            "amount": content_amount,
        },
        "sku": {
            "done": is_consumable or (bool(allocations) and all(a.get("skuId") for a in allocations)),
            "detail": (
                "耗材（本平台）无需吉客云货品" if is_consumable
                else (
                    f"{len(sku_linked)}/{len(allocations)} SKU 已关联吉客云"
                    if allocations else "无 SKU 明细"
                )
            ),
            "amount": None,
        },
        "jackyun_po": {
            "done": is_consumable or (bool(allocations) and all(a.get("skuId") for a in allocations)),
            "detail": (
                "耗材采购单已建立" if is_consumable
                else (
                    "本系统采购单已建立" if allocations and all(a.get("skuId") for a in allocations)
                    else "待完成本系统采购明细"
                )
            ),
            "amount": sum(a.get("amount") or 0 for a in allocations) or None,
        },
        "closeout": {
            "done": inbound_ready and invoice_done and paid_any,
            "detail": (
                ("耗材已入库" if consumable_received else f"入库 {len(inbound)}")
                + " / " + invoice_text
                + (" / 已付款" if paid_any else " / 未付款")
            ) if (inbound or consumable_received or invoice or settlement or paid_any) else "尚未触发",
            "amount": sum(i.get("amount") or 0 for i in invoice) or None,
        },
    }


def _first_undone_step(states: dict[str, dict]) -> str | None:
    """返回最早未完成步骤的 key；全部完成则 None。"""
    for step in WORKBENCH_STEPS:
        if not states[step["key"]]["done"]:
            return step["key"]
    return None


def closeout_stage(row: dict, step_states: dict[str, dict] | None = None) -> str:
    """收尾环节细分到具体卡点，供列表状态标签直接显示。"""
    states = step_states or _step_states(row)
    if states.get("closeout", {}).get("done"):
        return "done"
    consumable_received = bool((row.get("consumable") or {}).get("received"))
    if not consumable_received and not (row.get("inbound") or []):
        return "awaiting_inbound"
    if (row.get("invoiceOutstanding") or 0) > 0 or not (row.get("invoice") or []):
        return "awaiting_invoice"
    try:
        reported_paid = Decimal(str(row.get("paidAmount"))) > 0 if row.get("paidAmount") is not None else False
    except (TypeError, ValueError):
        reported_paid = False
    paid_any = any(s.get("paid") for s in (row.get("settlement") or [])) or bool(row.get("paidOn1688")) or reported_paid
    if not paid_any:
        return "awaiting_payment"
    return "open"


# ---------- 接口实现 ----------
def _funnel_from_rows(rows: list[dict]) -> dict:
    """基于已加载订单行计算漏斗，避免 summary/funnel 重复访问数据库。"""
    total = len(rows)
    done_count = {step["key"]: 0 for step in WORKBENCH_STEPS}
    for row in rows:
        for key, st in _step_states(row).items():
            if st["done"]:
                done_count[key] += 1
    steps = [
        {
            **step,
            "count": done_count[step["key"]],
            "pct": round(done_count[step["key"]] / total * 100) if total else 0,
        }
        for step in WORKBENCH_STEPS
    ]
    return {"total": total, "stepTotal": WORKBENCH_TOTAL, "steps": steps}


def _todos_from_rows(rows: list[dict]) -> dict:
    """基于已加载订单行计算待办，避免同一请求重复 chain_snapshot。"""
    total = len(rows)
    counts = {step["key"]: 0 for step in WORKBENCH_STEPS}
    pending_total = 0
    for row in rows:
        states = _step_states(row)
        first = _first_undone_step(states)
        if first is not None:
            counts[first] += 1
            pending_total += 1
    return {
        "total": total,
        "pending": pending_total,
        "items": [
            {**step, "count": counts[step["key"]]}
            for step in WORKBENCH_STEPS
        ],
    }


def funnel(db: Session) -> dict:
    """兼容独立漏斗接口；新工作台主页面从 summary 内直接读取同一份结果。"""
    pairs, pf = chain_snapshot(db)
    rows = [_order_row(db, o, ext, pf=pf) for o, ext in pairs]
    return _funnel_from_rows(rows)


def todos(db: Session) -> dict:
    """兼容独立待办接口；新工作台主页面从 summary 内直接读取同一份结果。"""
    pairs, pf = chain_snapshot(db)
    rows = [_order_row(db, o, ext, pf=pf) for o, ext in pairs]
    return _todos_from_rows(rows)


def _pending_queue(row: dict) -> str | None:
    """返回订单当前唯一的待处理队列，保证统计卡与圆环可以加总。"""
    allocations = row.get("allocations") or []
    consumable = row.get("consumable") or {}
    consumable_items = consumable.get("items") or []
    # 耗材单：HC 明细即采购内容（_order_row 已据此置 purchaseContentComplete），
    # 无需 1688 SKU 分摊，也不生成吉客云采购单，下一步是登记耗材入库。
    if not consumable_items and (
        not row.get("purchaseContentComplete") or not allocations or any(
            not allocation.get("skuId") for allocation in allocations
        )
    ):
        return "refine"

    inbound = row.get("inbound") or []
    # 有真实入库单即完成入库阶段；耗材映射未完成只保留为提示，不阻塞收尾状态。
    inbound_ready = bool(inbound) or bool(consumable.get("received"))
    if consumable_items:
        if not consumable.get("received"):
            return "inbound"
    else:
        # 本系统采购主单已经由 ExternalPurchaseOrder 承载；吉客云采购单是历史参考，
        # 不再作为入库前置条件。没有入库或入库尚未确认时，统一进入待入库。
        if not inbound or not inbound_ready:
            return "inbound"

    invoice_status = str(row.get("invoiceStatus") or "").strip().lower()
    invoice_outstanding = _view_decimal(row.get("invoiceOutstanding"))
    invoice_done = (
        bool(row.get("invoice"))
        and invoice_status == "done"
        and invoice_outstanding <= Decimal("0.005")
    )
    if inbound_ready and not invoice_done:
        return "invoice"
    return None


def _paid_amount(row: dict, cap: Any = None) -> Decimal:
    """合并结算与平台付款事实；金额计算全程 Decimal。"""
    settlement_paid = sum(
        (
            _view_decimal(item.get("paidAmount") or item.get("amount"))
            for item in row.get("settlement") or []
            if item.get("paid")
        ),
        Decimal("0"),
    )
    platform_paid = (
        _view_decimal(row.get("paidAmount") or row.get("amount"))
        if row.get("paidOn1688")
        else Decimal("0")
    )
    paid = max(settlement_paid, platform_paid)
    if cap is not None:
        cap_value = _view_decimal(cap)
        paid = min(paid, max(Decimal("0"), cap_value))
    return paid


def summary(db: Session) -> dict:
    """采购工作台统计指标，全部基于本地已落库事实。"""
    pairs, pf = chain_snapshot(db)
    rows = [_order_row(db, o, ext, pf=pf) for o, ext in pairs]
    today = datetime.now(ZoneInfo(settings.TZ)).date()
    consumable_nos = _consumable_source_nos(db)
    new_orders = 0
    pending_sku = 0
    pending_po = 0
    pending_inbound = 0
    pending_invoice = 0
    goods_orders = 0
    consumable_orders = 0
    goods_producing = 0
    goods_inbound = 0
    consumable_transit = 0
    consumable_inbound = 0
    transit_orders = 0
    completed_orders = 0
    paid_amount = Decimal("0")
    total_amount = Decimal("0")
    for row in rows:
        amount = _view_decimal(row.get("amount"))
        total_amount += amount
        paid_amount += _paid_amount(row, cap=amount)

        order_date = _parse_dt(row.get("orderDate"))
        if order_date is not None and order_date.date() == today:
            new_orders += 1
        queue = _pending_queue(row)
        if queue == "refine":
            pending_sku += 1
        elif queue == "po":
            pending_po += 1
        elif queue == "inbound":
            pending_inbound += 1
        elif queue == "invoice":
            pending_invoice += 1
        if queue is None:
            completed_orders += 1

        kind = _order_kind(
            row.get("orderNo"), row.get("supplier"), consumable_nos,
            row.get("orderKindOverride", ""), bool(row.get("consumable")),
        )
        purchase_status = str(row.get("purchaseStatus") or "").strip()
        inbound_ready = bool(row.get("inbound")) or bool((row.get("consumable") or {}).get("received"))
        in_transit = purchase_status in {"shipped", "arrived"}
        if in_transit:
            transit_orders += 1
        if kind == "goods":
            goods_orders += 1
            if purchase_status == "producing":
                goods_producing += 1
            if inbound_ready:
                goods_inbound += 1
        else:
            consumable_orders += 1
            if in_transit:
                consumable_transit += 1
            if inbound_ready:
                consumable_inbound += 1
    exception_refs = _exception_refs(db)
    exception_count = sum(
        1 for row in rows
        if _row_has_exception(row, exception_refs)
    )
    return {
        "totalOrders": len(rows),
        "newOrders": new_orders,
        "pendingSku": pending_sku,
        "pendingPo": pending_po,
        "pendingInbound": pending_inbound,
        "pendingInvoice": pending_invoice,
        "exceptionCount": exception_count,
        "goodsOrders": goods_orders,
        "consumableOrders": consumable_orders,
        "goodsProducing": goods_producing,
        "goodsInbound": goods_inbound,
        "consumableTransit": consumable_transit,
        "consumableInbound": consumable_inbound,
        "transitOrders": transit_orders,
        "completedOrders": completed_orders,
        "paidRate": round(paid_amount / total_amount * Decimal("100")) if total_amount else 0,
        "paidAmount": float(paid_amount.quantize(Decimal("0.01"))),
        "totalAmount": float(total_amount.quantize(Decimal("0.01"))),
        "funnel": _funnel_from_rows(rows),
        "todos": _todos_from_rows(rows),
    }


def _parse_dt(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _time_group_label(t: datetime | None, today: date) -> str:
    """按业务时区把下单时间分组成：今天 / 昨天 / 星期 / M月D日 / 未注时间。"""
    if t is None:
        return "未注时间"
    d = t.date() if isinstance(t, datetime) else t
    if d == today:
        return "今天"
    if d == today - timedelta(days=1):
        return "昨天"
    delta_days = (today - d).days
    if 0 < delta_days < 7:
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        return weekdays[d.weekday()]
    if d.year == today.year:
        return f"{d.month}月{d.day}日"
    return d.isoformat()


_VALID_STATUSES = {
    "all", "pending", "done", "order", "content", "sku", "jackyun_po", "closeout",
    "refine", "po", "inbound", "transit", "invoice", "exception",
}


def _exception_summary(rec: ExceptionRecord) -> dict[str, Any]:
    """把异常记录压缩成前端可直接展示的摘要（标题/原因/金额差异/建议处理）。"""
    detail = rec.detail if isinstance(rec.detail, dict) else {}
    paid = _number(detail.get("paidAmount"))
    inbound = _number(detail.get("inboundAmount"))
    difference = _number(detail.get("difference"))
    if paid is not None and inbound is not None:
        if difference is None:
            gap_text = "金额不一致"
        elif difference > 0:
            gap_text = f"入库金额比实付多 ¥{difference:,.2f}"
        elif difference < 0:
            gap_text = f"入库金额比实付少 ¥{abs(difference):,.2f}"
        else:
            gap_text = "金额一致"
        message = f"1688 实付 ¥{paid:,.2f}，实际入库金额 ¥{inbound:,.2f}，{gap_text}"
    else:
        message = str(detail.get("reason") or rec.title or "")
    return {
        "type": rec.type or rec.code or "",
        "title": rec.title or "",
        "status": rec.status or "",
        "severity": rec.severity or "",
        "reason": str(detail.get("reason") or ""),
        "actionRequired": str(detail.get("actionRequired") or ""),
        "paidAmount": paid,
        "inboundAmount": inbound,
        "difference": difference,
        "suggestedAdjustment": _number(detail.get("suggestedAdjustment")),
        "message": message,
        "createdAt": rec.created_at.isoformat() if rec.created_at else None,
    }


def _exception_refs(db: Session) -> tuple[dict[int, list[dict]], dict[int, list[dict]]]:
    """分别读取文件订单 ID 与工作流采购单 ID 的待处理异常，避免自增 ID 碰撞误标。

    已确认/已解决的异常保留在异常中心作为历史记录，但不再阻塞采购工作台。
    返回 (file_order_exceptions, purchase_order_exceptions)：键为对应表主键，
    值为该对象的异常摘要列表（供前端展示报错原因）；键成员判断与 set 等价。
    """
    rows = (
        db.query(ExceptionRecord)
        .filter(ExceptionRecord.status == "pending")
        .all()
    )
    file_orders: dict[int, list[dict]] = {}
    purchase_orders: dict[int, list[dict]] = {}
    for row in rows:
        summary = _exception_summary(row)
        if row.ref_table == "alibaba1688_orders" and row.ref_id:
            try:
                file_orders.setdefault(int(row.ref_id), []).append(summary)
            except (TypeError, ValueError):
                continue
            continue
        if row.ref_table == "external_purchase_orders" and row.ref_id:
            try:
                purchase_orders.setdefault(int(row.ref_id), []).append(summary)
            except (TypeError, ValueError):
                continue
            continue
        # 兼容没有明确 ref_table 的历史异常；有明确 ref_table 时不读取混合 detail。
        detail = row.detail or {}
        for key in ("orderId", "order_id"):
            value = detail.get(key)
            if value is not None:
                try:
                    file_orders.setdefault(int(value), []).append(summary)
                except (TypeError, ValueError):
                    pass
        for key in ("poId", "po_id"):
            value = detail.get(key)
            if value is not None:
                try:
                    purchase_orders.setdefault(int(value), []).append(summary)
                except (TypeError, ValueError):
                    pass
    return file_orders, purchase_orders


def _row_has_exception(row: dict, exception_refs: tuple[dict[int, list[dict]], dict[int, list[dict]]]) -> bool:
    file_order_ids, purchase_order_ids = exception_refs
    return row.get("orderId") in file_order_ids or row.get("externalPoId") in purchase_order_ids


def _row_exception_info(row: dict, exception_refs: tuple[dict[int, list[dict]], dict[int, list[dict]]]) -> list[dict]:
    """返回该订单命中的异常摘要列表（空列表 = 无异常）。"""
    file_orders, purchase_orders = exception_refs
    items: list[dict] = []
    if row.get("orderId") is not None:
        items.extend(file_orders.get(row["orderId"], []))
    if row.get("externalPoId") is not None:
        items.extend(purchase_orders.get(row["externalPoId"], []))
    return items


def _matches_status(row: dict, states: dict[str, dict], first: str | None, status: str,
                    exception_refs: tuple[dict[int, list[dict]], dict[int, list[dict]]]) -> bool:
    if status == "all":
        return True
    if status == "done":
        return _pending_queue(row) is None
    if status in ("refine", "content", "sku"):
        return _pending_queue(row) == "refine"
    if status in ("po", "jackyun_po"):
        return _pending_queue(row) == "po"
    if status == "inbound":
        return _pending_queue(row) == "inbound"
    if status == "invoice":
        return _pending_queue(row) == "invoice"
    if status == "exception":
        return _row_has_exception(row, exception_refs)
    # 在途：与 summary 的 transitOrders 同口径（已发货/已到货未入库）。
    if status == "transit":
        return str(row.get("purchaseStatus") or "").strip() in {"shipped", "arrived"}
    if status == "pending":
        return first is not None
    return first == status


def list_orders(
    db: Session,
    status: str = "all",
    q: str = "",
    sort_by: str = "date",
    page: int = 1,
    page_size: int = 20,
    start_date: date | None = None,
    end_date: date | None = None,
    channel: str = "all",
    kind: str = "all",
    warehouse: str = "",
) -> dict:
    """按下单时间倒序、按时间标签分组的订单列表；支持状态和日期筛选。"""
    status = status if status in _VALID_STATUSES else "all"
    pairs, pf = chain_snapshot(db)
    rows = [_order_row(db, o, ext, pf=pf) for o, ext in pairs]
    today = datetime.now(ZoneInfo(settings.TZ)).date()
    exception_refs = _exception_refs(db)
    consumable_nos = _consumable_source_nos(db)
    channel = channel if channel in {"all", "1688", "pdd", "taobao", "other"} else "all"
    kind = kind if kind in {"all", "goods", "consumable"} else "all"
    warehouse = warehouse.strip()
    out: list[dict] = []
    for row in rows:
        states = _step_states(row)
        first = _first_undone_step(states)
        first_step = next((s for s in WORKBENCH_STEPS if s["key"] == first), None)
        order_kind = _order_kind(
            row.get("orderNo"), row.get("supplier"), consumable_nos,
            row.get("orderKindOverride", ""), bool(row.get("consumable")),
        )
        list_fields = _supply_chain_list_fields(row)
        order_date = _parse_dt(row.get("orderDate"))
        order_day = order_date.date() if order_date is not None else None
        if start_date is not None and (order_day is None or order_day < start_date):
            continue
        if end_date is not None and (order_day is None or order_day > end_date):
            continue
        if not _matches_status(row, states, first, status, exception_refs):
            continue
        if channel != "all" and _channel_key(row.get("platform")) != channel:
            continue
        if kind != "all" and order_kind != kind:
            continue
        if warehouse and list_fields["warehouseName"] != warehouse:
            continue
        if q:
            ql = q.lower()
            sku_text = " ".join(f"{a.get('skuCode', '')} {a.get('goodsName', '')}" for a in row.get("allocations", []))
            hay = f"{row.get('orderNo','')} {row.get('supplier','')} {row.get('title','')} {sku_text}".lower()
            if ql not in hay:
                continue
        out.append({
            "orderId": row["fileOrderId"] if row["fileOrderId"] is not None else -row["externalPoId"],
            "externalPoId": row.get("externalPoId"),
            "orderNo": row["orderNo"],
            "platform": row.get("platform") or "other",
            "orderKind": order_kind,
            "supplier": row.get("supplier") or "",
            "amount": row.get("amount"),
            "paidAmount": row.get("paidAmount"),
            "freight": row.get("freight"),
            "orderDate": row.get("orderDate"),
            "orderStatus": row.get("orderStatus") or "",
            "purchaseStatus": row.get("purchaseStatus") or "",
            "hasException": _row_has_exception(row, exception_refs),
            "exceptionInfo": _row_exception_info(row, exception_refs),
            "doneCount": row.get("doneCount") or 0,
            "stageTotal": row.get("stageTotal") or WORKBENCH_TOTAL,
            "firstUndone": first,
        "firstUndoneLabel": first_step["label"] if first_step else "开票完成",
        "firstUndoneShort": first_step["short"] if first_step else "开票完成",
            "firstUndoneDimension": first_step["dimension"] if first_step else "purchase",
            "stepStates": states,
            "inboundDone": bool(row.get("inbound")) or bool((row.get("consumable") or {}).get("received")),
            "invoiceDone": bool(row.get("invoice")),
            "invoiceStatus": row.get("invoiceStatus") or "none",
            "invoicedAmount": row.get("invoicedAmount"),
            "invoiceOutstanding": row.get("invoiceOutstanding"),
            "closeoutStage": closeout_stage(row, states),
            "remark": row.get("adjustmentNote") or row.get("title") or "",
            **list_fields,
        })

    if sort_by == "amount":
        out.sort(key=lambda r: (r.get("amount") or 0), reverse=True)
    elif sort_by == "invoice":
        out.sort(key=lambda r: (r.get("invoiceOutstanding") or 0), reverse=True)
    elif sort_by == "status":
        out.sort(key=lambda r: r.get("doneCount") or 0, reverse=True)
    else:
        out.sort(key=lambda r: r.get("orderDate") or "", reverse=True)

    total_filtered = len(out)
    start = max(0, (page - 1) * page_size)
    paged = out[start:start + page_size]

    group_order: list[str] = []
    groups: dict[str, list[dict]] = {}
    for item in paged:
        label = _time_group_label(_parse_dt(item.get("orderDate")), today)
        if label not in groups:
            group_order.append(label)
            groups[label] = []
        groups[label].append(item)

    return {
        "total": total_filtered,
        "page": page,
        "pageSize": page_size,
        "invoiceOutstandingTotal": round(sum(r.get("invoiceOutstanding") or 0 for r in out), 2),
        "groups": [{"label": k, "items": groups[k]} for k in group_order],
    }


def workbench(db: Session, order_id: int) -> dict | None:
    """选中订单的工作面板：5 步骤每步的状态 + 关键明细 + 跳转入口。"""
    pairs, pf = chain_snapshot(db)
    order: Alibaba1688Order | None = None
    external: ExternalPurchaseOrder | None = None
    for candidate_order, candidate_external in pairs:
        candidate_id = candidate_order.id if candidate_order is not None else -candidate_external.id
        if candidate_id == order_id:
            order, external = candidate_order, candidate_external
            break
    if order is None and external is None:
        return None
    if order is None and external is not None:
        row = _order_row(db, None, external, pf=pf)
    else:
        row = _order_row(db, order, external, pf=pf)
    _enrich_allocation_inventory(db, row)
    states = _step_states(row)
    closeout_stage_value = closeout_stage(row, states)
    supplier = row.get("supplier") or ""
    exception_refs = _exception_refs(db)
    consumable_nos = _consumable_source_nos(db)
    return {
        "order": {
            "orderId": order_id,
            "fileOrderId": row.get("fileOrderId"),
            "externalPoId": row.get("externalPoId"),
            "orderNo": row["orderNo"],
            "platform": row.get("platform") or "other",
            "orderKind": _order_kind(row.get("orderNo"), row.get("supplier"), consumable_nos, row.get("orderKindOverride", ""), bool(row.get("consumable"))),
            "orderKindOverride": row.get("orderKindOverride", ""),
            "supplier": supplier,
            "buyer": row.get("buyer"),
            "amount": row.get("amount"),
            "goodsTotal": row.get("goodsTotal"),
            "freight": row.get("freight"),
            "discount": row.get("discount"),
            "paidAmount": row.get("paidAmount"),
            "paidOn1688": bool(row.get("paidOn1688")),
            "paidOn1688At": row.get("paidOn1688At"),
            "adjustmentAmount": row.get("adjustmentAmount"),
            "adjustmentNote": row.get("adjustmentNote"),
            "orderDate": row.get("orderDate"),
            "orderStatus": row.get("orderStatus"),
            "purchaseStatus": row.get("purchaseStatus") or "",
            "closeoutStage": closeout_stage_value,
            "jackyunPoBypassed": bool(external is not None and (external.raw or {}).get("jackyunPoBypassed")),
            "invoiceStatus": row.get("invoiceStatus") or "none",
            "invoicedAmount": row.get("invoicedAmount"),
            "invoiceOutstanding": row.get("invoiceOutstanding"),
            "title": row.get("title"),
            "hasException": _row_has_exception(row, exception_refs),
            "exceptionInfo": _row_exception_info(row, exception_refs),
            "logistics": row.get("logistics") or {},
            "shipStatus": row.get("shipStatus") or "",
        },
        "stepStates": states,
        "detail": {
            "allocations": row.get("allocations") or [],
            "orderItems": row.get("orderItems") or [],
            "consumable": row.get("consumable"),
            "expenses": row.get("expenses") or [],
            "purchaseOrders": row.get("purchaseOrders") or [],
            "poAmountClosure": row.get("poAmountClosure"),
            "inbound": row.get("inbound") or [],
            "invoice": row.get("invoice") or [],
            "settlement": row.get("settlement") or [],
            "unallocatedAmount": row.get("unallocatedAmount"),
        },
        "stepTotal": WORKBENCH_TOTAL,
        "warehouse": _warehouse_block(db, row),
        "supplierHistory": order_supplier_history(db, supplier, exclude_order_id=row["orderId"]) if supplier else None,
    }


def _warehouse_block(db: Session, row: dict[str, Any]) -> dict[str, Any]:
    """仓库信息卡：目标仓库 + 吉客云仓库ID + 是否可售 + 当前库存 + 在途数量。

    全部来自本地已落库事实：仓库主档（Warehouses）、独立运算库存（采购入库 − 销售出库）、
    订单 SKU 分配与入库状态。缺失数据如实返回 None / 0，不伪造。
    """
    inbound = row.get("inbound") or []
    consumable = row.get("consumable") or {}
    actual_warehouse_name = str(
        (inbound[0].get("warehouseName") if inbound else "")
        or consumable.get("warehouseName")
        or (consumable.get("location") if consumable.get("location") not in {"own", "factory"} else "")
        or ""
    )
    target_warehouse_id = row.get("targetWarehouseId")
    target_warehouse_name = str(row.get("targetWarehouseName") or "")
    warehouse_name = str(
        actual_warehouse_name or target_warehouse_name
    )
    allocations = row.get("allocations") or []
    sku_ids = [a.get("skuId") for a in allocations if a.get("skuId")]
    ordered_qty = sum(
        (_view_decimal(a.get("quantity")) for a in allocations),
        Decimal("0"),
    )

    wh = db.query(Warehouse).filter(Warehouse.name == warehouse_name).first() if warehouse_name else None
    jackyun_wh_id = (wh.jackyun_warehouse_id or "") if wh else ""
    is_sellable = bool(wh.is_sellable) if wh else None

    current_stock_value: Decimal | None = None
    if wh is not None and sku_ids:
        from app.services.inventory_position_service import current_positions
        positions = current_positions(db)
        current_stock_value = sum(
            (
                positions["by_sku_warehouse"].get(sku_id, {}).get(wh.id, Decimal("0"))
                for sku_id in sku_ids
            ),
            Decimal("0"),
        )

    purchase_status = str(row.get("purchaseStatus") or "")
    inbound_done = bool(inbound) or bool(consumable.get("received"))
    # 在途数量：已发货/到货但尚未入库时，等于本单分配数量；其余为 0。
    in_transit = (
        Decimal("0")
        if inbound_done
        else (ordered_qty if purchase_status in {"shipped", "arrived"} else Decimal("0"))
    )

    return {
        "warehouseName": warehouse_name,
        "warehouseId": wh.id if wh is not None else None,
        "targetWarehouseId": target_warehouse_id,
        "targetWarehouseName": target_warehouse_name,
        "jackyunWarehouseId": jackyun_wh_id,
        "isSellable": is_sellable,
        "currentStock": float(current_stock_value) if current_stock_value is not None else None,
        "inTransitQty": float(in_transit),
    }


def _enrich_allocation_inventory(db: Session, row: dict[str, Any]) -> None:
    """给详情中的每条采购明细补充其对应仓库的当前库存。

    订单可能包含多个 SKU 或多张入库单，订单级 ``warehouse.currentStock`` 只是总览，
    不能直接复用到每一行；这里按「分配行 → 入库单 → 仓库 → SKU」逐行计算。
    """
    allocations = row.get("allocations") or []
    if not allocations:
        return

    from app.services.inventory_position_service import current_positions

    positions = current_positions(db)
    warehouse_rows = db.query(Warehouse).all()
    warehouse_lookup: dict[str, Warehouse] = {}
    for warehouse in warehouse_rows:
        for value in (warehouse.name, warehouse.code, warehouse.jackyun_warehouse_id):
            key = str(value or "").strip().lower()
            if key:
                warehouse_lookup.setdefault(key, warehouse)

    inbound_by_id = {
        int(item.get("documentId")) if item.get("documentId") is not None else int(item.get("targetId")):
        item
        for item in row.get("inbound") or []
        if item.get("documentId") is not None or item.get("targetId") is not None
    }
    inbound = row.get("inbound") or []
    consumable = row.get("consumable") or {}
    default_name = str(
        (inbound[0].get("warehouseName") if inbound else "")
        or consumable.get("warehouseName")
        or ""
    )
    default_warehouse = warehouse_lookup.get(default_name.strip().lower()) if default_name else None

    for allocation in allocations:
        raw_sku_id = allocation.get("skuId")
        try:
            sku_id = int(raw_sku_id) if raw_sku_id is not None else None
        except (TypeError, ValueError):
            sku_id = None
        inbound_id = allocation.get("inboundDocumentId")
        inbound_row = inbound_by_id.get(int(inbound_id)) if inbound_id is not None else None
        warehouse_name = str(
            (inbound_row or {}).get("warehouseName")
            or default_name
            or ""
        )
        warehouse = warehouse_lookup.get(warehouse_name.strip().lower()) if warehouse_name else default_warehouse
        allocation["warehouseId"] = warehouse.id if warehouse is not None else None
        allocation["warehouseName"] = warehouse.name if warehouse is not None else warehouse_name
        if positions["last_document_at"] is None or sku_id is None or warehouse is None:
            allocation["currentStock"] = None
            continue
        allocation["currentStock"] = float(
            positions["by_sku_warehouse"].get(sku_id, {}).get(warehouse.id, Decimal("0"))
        )


def suppliers(db: Session, limit: int = 200, offset: int = 0) -> dict:
    """供应商聚合列表（管理视角），直接复用 procurement_chain_service 的聚合。"""
    return supplier_summaries(db, limit=limit, offset=offset)


def supplier_workbench(db: Session, supplier_name: str) -> dict | None:
    """兼容旧名称路径；V2 页面优先使用 supplier_workbench_by_partner。"""
    return supplier_detail(db, supplier_name)


def supplier_workbench_by_partner(db: Session, partner_id: int) -> dict | None:
    """按 canonical BusinessPartner ID 读取供应商历史，不再依赖名称 join。"""
    return supplier_detail(db, partner_id=partner_id)
