"""供应商主档自动回补与幂等建档。"""

from __future__ import annotations

import unicodedata
from typing import Any

from sqlalchemy.orm import Session

from app.models.alibaba1688_import import Alibaba1688FileImport, Alibaba1688Order
from app.models.purchase import ExternalPurchaseOrder, JackyunPurchaseOrder, JackyunPurchaseOrderLink, Supplier


_PLATFORM_PRIORITY = {
    "": 0,
    "其他": 1,
    "线下": 2,
    "淘宝": 3,
    "拼多多": 3,
    "1688": 4,
}


def normalize_supplier_name(value: Any) -> str:
    """只清理展示名首尾和全角空白，不合并可能不同的公司名称。"""
    if value is None:
        return ""
    return " ".join(unicodedata.normalize("NFKC", str(value)).split()).strip()


def supplier_platform(value: Any, *, fallback: str = "其他") -> str:
    """将业务来源映射到供应商档案当前支持的渠道名称。"""
    raw = str(value or "").strip().lower()
    if raw == "1688" or "阿里" in raw:
        return "1688"
    if raw in {"pdd", "pinduoduo"} or "拼多多" in raw:
        return "拼多多"
    if raw in {"taobao", "tmall"} or "淘宝" in raw or "天猫" in raw:
        return "淘宝"
    if raw in {"offline", "manual", "线下"}:
        return "线下"
    return fallback


def ensure_supplier(
    db: Session,
    name: Any,
    *,
    platform: Any = "",
) -> tuple[Supplier | None, bool, bool]:
    """确保供应商存在，返回 ``(row, created, updated)``。

    只按清理后的完整名称匹配，避免把简称、不同主体或不同工厂误合并。
    自动同步只补平台字段，不触碰税号、联系人、电话、地址和临时标记。
    """
    clean_name = normalize_supplier_name(name)
    if not clean_name:
        return None, False, False

    clean_platform = supplier_platform(platform)
    row = db.query(Supplier).filter(Supplier.name == clean_name).order_by(Supplier.id).first()
    if row is None:
        row = Supplier(
            name=clean_name,
            platform=clean_platform,
            is_temp=False,
        )
        db.add(row)
        # 部分批处理会关闭 autoflush；立即 flush 让同一批后续来源复用这条主档。
        db.flush()
        return row, True, False

    current_platform = normalize_supplier_name(row.platform)
    if _PLATFORM_PRIORITY.get(clean_platform, 1) > _PLATFORM_PRIORITY.get(current_platform, 0):
        row.platform = clean_platform
        return row, False, True
    return row, False, False


def sync_suppliers_from_business_data(db: Session) -> dict[str, Any]:
    """只从真实采购订单事实幂等回补供应商主档。

    入库、结算、退货、报销/费用以及生产执行单都属于采购后的下游事实，
    不能反向创建供应商；否则会把非采购付款对象带进供应商档案。
    """
    created = 0
    updated = 0
    sources = {
        "purchaseOrders": 0,
        "1688Orders": 0,
        "jackyunPurchaseOrders": 0,
    }

    def add(name: Any, platform: Any, source: str) -> None:
        nonlocal created, updated
        row, was_created, was_updated = ensure_supplier(db, name, platform=platform)
        if row is None:
            return
        sources[source] += 1
        created += int(was_created)
        updated += int(was_updated)

    for row in db.query(ExternalPurchaseOrder).all():
        if (row.raw or {}).get("referenceOnly") is True:
            continue
        add(row.supplier_name, row.platform, "purchaseOrders")

    # 独立吉客云采购单同样属于真实采购事实。已经挂到 External/1688 工作流的
    # 吉客云采购单是同一笔采购的下游单据，不重复创建第二个供应商主档。
    linked_jackyun_ids = {
        int(link.jackyun_po_id)
        for link in db.query(JackyunPurchaseOrderLink).all()
    }
    for row in db.query(JackyunPurchaseOrder).order_by(JackyunPurchaseOrder.id).all():
        status = str(row.status or "").strip().lower()
        if row.id in linked_jackyun_ids or any(token in status for token in ("cancel", "取消", "作废", "void")):
            continue
        add(row.supplier_name, "其他", "jackyunPurchaseOrders")

    # 1688 已删除批次不再自动生成新供应商；采购工作流主档仍保留的供应商不会被删除。
    for row in (
        db.query(Alibaba1688Order)
        .join(Alibaba1688FileImport, Alibaba1688Order.import_id == Alibaba1688FileImport.id)
        .filter(
            Alibaba1688Order.row_status == "active",
            Alibaba1688FileImport.lifecycle == "active",
        )
        .all()
    ):
        add(row.seller_company_name or row.seller_member_name, "1688", "1688Orders")

    return {
        "created": created,
        "updated": updated,
        "sources": sources,
    }
