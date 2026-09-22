from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.catalog import InventorySnapshot, Warehouse
from app.models.consumable import ConsumableTransaction
from app.models.consumable_purchase import ConsumableReceipt

WAREHOUSE_TYPES = {"factory", "b2c", "other"}
PURPOSES = {"goods", "consumable", "both"}
STATUSES = {"active", "inactive"}


def display_code(row: Warehouse) -> str:
    return (row.code or (f"JKY-{row.jackyun_warehouse_id}" if row.jackyun_warehouse_id else f"WH-{row.id}")).upper()


def resolve_warehouse_reference(
    db: Session, *, code: str | None = "", name: str | None = "",
) -> tuple[str, str]:
    """把外部文件里的仓库编号或仓库名归一为本系统仓库档案。"""
    source_code = str(code or "").strip()
    source_name = str(name or "").strip()
    candidates: dict[str, list[Warehouse]] = {}
    for row in db.query(Warehouse).all():
        for value in (row.jackyun_warehouse_id, row.code, row.name):
            key = str(value or "").strip().casefold()
            if not key:
                continue
            bucket = candidates.setdefault(key, [])
            if row not in bucket:
                bucket.append(row)

    for value in (source_code, source_name):
        bucket = candidates.get(value.casefold()) if value else None
        if bucket and len(bucket) == 1:
            row = bucket[0]
            return display_code(row), row.name.strip() or source_name or source_code

    return source_code, source_name


def serialize(row: Warehouse) -> dict:
    return {
        "id": row.id,
        "code": display_code(row),
        "name": row.name,
        "warehouseType": row.warehouse_type or "other",
        "purpose": row.purpose or "both",
        "isSellable": bool(row.is_sellable),
        "status": row.status,
        "note": row.note or "",
        "jackyunWarehouseId": row.jackyun_warehouse_id,
        "source": "jackyun" if row.jackyun_warehouse_id else "local",
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_warehouses(db: Session, *, include_inactive: bool = True) -> list[dict]:
    query = db.query(Warehouse)
    if not include_inactive:
        query = query.filter(Warehouse.status == "active")
    rows = query.order_by(Warehouse.status.desc(), Warehouse.id).all()
    return [serialize(row) for row in rows]


def _normalize(
    *, code: str, name: str, warehouse_type: str, purpose: str,
    is_sellable: bool, status: str, note: str,
    jackyun_warehouse_id: str | None = None,
) -> dict:
    code = code.strip().upper()
    name = name.strip()
    warehouse_type = warehouse_type.strip().lower()
    purpose = purpose.strip().lower()
    status = status.strip().lower()
    note = note.strip()
    jackyun_warehouse_id = (jackyun_warehouse_id or "").strip() or None
    if not code:
        raise ValueError("仓库编码不能为空")
    if not name:
        raise ValueError("仓库名称不能为空")
    if warehouse_type not in WAREHOUSE_TYPES:
        raise ValueError("仓库类型必须是 factory / b2c / other")
    if purpose not in PURPOSES:
        raise ValueError("仓库用途必须是 goods / consumable / both")
    if status not in STATUSES:
        raise ValueError("仓库状态必须是 active / inactive")
    return {
        "code": code,
        "name": name,
        "warehouse_type": warehouse_type,
        "purpose": purpose,
        "is_sellable": bool(is_sellable),
        "status": status,
        "note": note,
        "jackyun_warehouse_id": jackyun_warehouse_id,
    }


def _check_unique(db: Session, values: dict, *, row_id: int | None = None) -> None:
    code_query = db.query(Warehouse).filter(Warehouse.code == values["code"])
    if row_id is not None:
        code_query = code_query.filter(Warehouse.id != row_id)
    if code_query.first():
        raise ValueError("仓库编码已存在")
    external = values.get("jackyun_warehouse_id")
    if external:
        external_query = db.query(Warehouse).filter(Warehouse.jackyun_warehouse_id == external)
        if row_id is not None:
            external_query = external_query.filter(Warehouse.id != row_id)
        if external_query.first():
            raise ValueError("该吉客云仓库ID已经绑定到其他仓库")


def create_warehouse(
    db: Session, *, code: str, name: str, warehouse_type: str = "other",
    purpose: str = "both", is_sellable: bool = False,
    status: str = "active", note: str = "", jackyun_warehouse_id: str | None = None,
) -> Warehouse:
    values = _normalize(
        code=code, name=name, warehouse_type=warehouse_type, purpose=purpose,
        is_sellable=is_sellable, status=status, note=note,
        jackyun_warehouse_id=jackyun_warehouse_id,
    )
    _check_unique(db, values)
    row = Warehouse(**values, raw={})
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_warehouse(
    db: Session, warehouse_id: int, *, code: str | None = None,
    name: str | None = None, warehouse_type: str | None = None,
    purpose: str | None = None, is_sellable: bool | None = None,
    status: str | None = None, note: str | None = None,
    jackyun_warehouse_id: str | None = None,
) -> Warehouse:
    row = db.get(Warehouse, warehouse_id)
    if row is None:
        raise ValueError("仓库不存在")
    values = _normalize(
        code=display_code(row) if code is None else code,
        name=row.name if name is None else name,
        warehouse_type=(row.warehouse_type or "other") if warehouse_type is None else warehouse_type,
        purpose=(row.purpose or "both") if purpose is None else purpose,
        is_sellable=row.is_sellable if is_sellable is None else is_sellable,
        status=row.status if status is None else status,
        note=(row.note or "") if note is None else note,
        jackyun_warehouse_id=row.jackyun_warehouse_id if jackyun_warehouse_id is None else jackyun_warehouse_id,
    )
    _check_unique(db, values, row_id=row.id)
    for key, value in values.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


def delete_warehouse(db: Session, warehouse_id: int) -> Warehouse:
    """物理删除没有任何业务引用的仓库，历史仓库只能停用。"""
    row = db.get(Warehouse, warehouse_id)
    if row is None:
        raise LookupError("仓库不存在")

    references = {
        "库存快照": db.query(func.count(InventorySnapshot.id)).filter(InventorySnapshot.warehouse_id == warehouse_id).scalar() or 0,
        "耗材收货单": db.query(func.count(ConsumableReceipt.id)).filter(ConsumableReceipt.warehouse_id == warehouse_id).scalar() or 0,
        "耗材库存流水": db.query(func.count(ConsumableTransaction.id)).filter(ConsumableTransaction.warehouse_id == warehouse_id).scalar() or 0,
    }
    used_by = [f"{label} {count} 条" for label, count in references.items() if count]
    if used_by:
        usage_text = "、".join(used_by)
        raise ValueError(f"仓库「{row.name}」已被{usage_text}引用，不能物理删除；如不再使用请先停用。")

    db.delete(row)
    db.commit()
    return row


def get_active(db: Session, warehouse_id: int) -> Warehouse:
    row = db.get(Warehouse, warehouse_id)
    if row is None or row.status != "active":
        raise ValueError("所选仓库不存在或已停用")
    return row


def default_for_type(db: Session, warehouse_type: str) -> Warehouse | None:
    return (
        db.query(Warehouse)
        .filter(Warehouse.status == "active", Warehouse.warehouse_type == warehouse_type)
        .order_by(Warehouse.id)
        .first()
    )


def active_for_purpose(db: Session, purpose: str) -> list[Warehouse]:
    """返回当前可承接指定库存用途的启用仓库。"""
    return (
        db.query(Warehouse)
        .filter(
            Warehouse.status == "active",
            Warehouse.purpose.in_((purpose, "both")),
        )
        .order_by(Warehouse.id)
        .all()
    )


def default_for_consumable(db: Session) -> Warehouse | None:
    """仅在耗材仓唯一时提供默认仓，避免无提示地写入错误仓库。"""
    rows = active_for_purpose(db, "consumable")
    return rows[0] if len(rows) == 1 else None


def legacy_location(row: Warehouse) -> str:
    """兼容旧耗材双库存字段：工厂仓→factory，其余仓暂映射 own。"""
    return "factory" if row.warehouse_type == "factory" else "own"
