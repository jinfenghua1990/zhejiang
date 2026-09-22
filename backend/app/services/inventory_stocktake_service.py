from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.catalog import Product, ProductSku, Warehouse
from app.models.consumable import Consumable
from app.models.inventory_stocktake import InventoryStocktakeItem, InventoryStocktakeTask
from app.services import consumable_service, inventory_position_service, warehouse_service
from app.utils.money import to_decimal


KINDS = {"goods", "consumable"}
SCOPES = {"all", "partial"}
OPEN_STATUSES = {"pending", "counting", "review"}


def _qty(value: Decimal | None) -> str:
    return f"{to_decimal(value):.4f}"


def _warehouse(db: Session, warehouse_id: int) -> Warehouse:
    row = db.get(Warehouse, warehouse_id)
    if row is None or row.status != "active":
        raise ValueError("所选仓库不存在或已停用")
    return row


def _allowed_kinds(warehouse: Warehouse) -> set[str]:
    purpose = warehouse.purpose or "both"
    if purpose == "goods":
        return {"goods"}
    if purpose == "consumable":
        return {"consumable"}
    return {"goods", "consumable"}


def _normalize_kinds(warehouse: Warehouse, item_kinds: list[str]) -> list[str]:
    kinds = [str(value).strip() for value in item_kinds if str(value).strip()]
    if not kinds:
        raise ValueError("至少选择一种盘点类型")
    invalid = set(kinds) - KINDS
    if invalid:
        raise ValueError("盘点类型只能是正品或耗材")
    denied = set(kinds) - _allowed_kinds(warehouse)
    if denied:
        raise ValueError(f"仓库“{warehouse.name}”的用途不允许盘点所选类型")
    return list(dict.fromkeys(kinds))


def _consumable_aggregate_location(db: Session, warehouse: Warehouse) -> str:
    """耗材当前仍以 own/factory 双口径汇总；只有同口径唯一仓库时才允许精确盘点。"""
    location = warehouse_service.legacy_location(warehouse)
    matching = [
        row for row in db.query(Warehouse).filter(Warehouse.status == "active").all()
        if row.purpose in {"consumable", "both"} and warehouse_service.legacy_location(row) == location
    ]
    if len(matching) != 1 or matching[0].id != warehouse.id:
        names = "、".join(row.name for row in matching[:5]) or "无"
        label = "工厂仓" if location == "factory" else "自有仓"
        raise ValueError(
            f"耗材库存当前仍按“{label}”汇总，发现同口径仓库 {len(matching)} 个（{names}），"
            "无法安全拆分账面数。请先保持该口径唯一仓库，或后续升级为完整按仓核算。"
        )
    return location


def stocktake_candidates(
    db: Session,
    *,
    warehouse_id: int,
    item_kinds: list[str],
    search: str = "",
    category: str = "",
) -> list[dict]:
    warehouse = _warehouse(db, warehouse_id)
    kinds = _normalize_kinds(warehouse, item_kinds)
    term = search.strip()
    category = category.strip()
    result: list[dict] = []

    if "goods" in kinds:
        positions = inventory_position_service.current_positions(db)
        query = (
            db.query(ProductSku, Product)
            .outerjoin(Product, Product.id == ProductSku.product_id)
            .filter(ProductSku.status == "active", ProductSku.product_type != "virtual_bundle")
        )
        if term:
            pattern = f"%{term}%"
            query = query.filter(or_(
                ProductSku.sku_code.ilike(pattern),
                ProductSku.sku_name.ilike(pattern),
                ProductSku.barcode.ilike(pattern),
                Product.goods_name.ilike(pattern),
            ))
        if category:
            query = query.filter(Product.category == category)
        for sku, product in query.order_by(ProductSku.sku_code, ProductSku.id).limit(1000).all():
            book = to_decimal(
                positions.get("by_sku_warehouse", {}).get(sku.id, {}).get(warehouse.id, Decimal("0"))
            )
            result.append({
                "kind": "goods",
                "id": sku.id,
                "code": sku.sku_code,
                "name": sku.sku_name or (product.goods_name if product else ""),
                "goodsName": product.goods_name if product else "",
                "category": product.category if product else "",
                "unit": sku.unit or "",
                "bookQty": _qty(book),
                "warehouseId": warehouse.id,
                "warehouseName": warehouse.name,
            })

    if "consumable" in kinds:
        location = _consumable_aggregate_location(db, warehouse)
        query = db.query(Consumable).filter(Consumable.status == "active")
        if term:
            pattern = f"%{term}%"
            query = query.filter(or_(
                Consumable.code.ilike(pattern),
                Consumable.name.ilike(pattern),
                Consumable.barcode.ilike(pattern),
            ))
        if category:
            query = query.filter(Consumable.category == category)
        for row in query.order_by(Consumable.code, Consumable.id).limit(1000).all():
            book = to_decimal(row.factory_qty if location == "factory" else row.stock_qty)
            result.append({
                "kind": "consumable",
                "id": row.id,
                "code": row.code,
                "name": row.name,
                "goodsName": "",
                "category": row.category or "",
                "unit": row.unit or "",
                "bookQty": _qty(book),
                "warehouseId": warehouse.id,
                "warehouseName": warehouse.name,
            })

    return result


def _task_number() -> str:
    now = datetime.now(timezone.utc)
    return f"PD{now.astimezone().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:5].upper()}"


def create_task(
    db: Session,
    *,
    scope: str,
    warehouse_id: int,
    item_kinds: list[str],
    selected_items: list[dict] | None = None,
    search: str = "",
    category: str = "",
    note: str = "",
    actor: str = "system",
) -> InventoryStocktakeTask:
    scope = scope.strip().lower()
    if scope not in SCOPES:
        raise ValueError("盘点范围必须是全部盘点或部分盘点")
    warehouse = _warehouse(db, warehouse_id)
    kinds = _normalize_kinds(warehouse, item_kinds)

    candidates = stocktake_candidates(
        db,
        warehouse_id=warehouse.id,
        item_kinds=kinds,
        search=search if scope == "partial" else "",
        category=category if scope == "partial" else "",
    )
    by_key = {(item["kind"], int(item["id"])): item for item in candidates}
    if scope == "partial":
        refs = {
            (str(item.get("kind") or ""), int(item.get("id") or 0))
            for item in (selected_items or [])
            if item.get("kind") and item.get("id")
        }
        if not refs:
            raise ValueError("部分盘点至少选择一个货品或耗材")
        missing = [key for key in refs if key not in by_key]
        if missing:
            raise ValueError("部分盘点包含已失效、被过滤或不属于当前仓库用途的项目")
        candidates = [by_key[key] for key in refs]

    if not candidates:
        raise ValueError("当前条件下没有可盘点项目")
    if len(candidates) > 2000:
        raise ValueError("单个盘点任务最多 2000 行，请改用部分盘点拆分处理")

    task = InventoryStocktakeTask(
        number=_task_number(),
        scope=scope,
        status="pending",
        warehouse_id=warehouse.id,
        item_kinds=kinds,
        search_text=search.strip(),
        category_filter=category.strip(),
        note=note.strip(),
        created_by=actor,
    )
    db.add(task)
    db.flush()
    for candidate in candidates:
        db.add(InventoryStocktakeItem(
            task_id=task.id,
            item_kind=candidate["kind"],
            ref_id=int(candidate["id"]),
            warehouse_id=warehouse.id,
            code=candidate["code"],
            name=candidate["name"],
            category=candidate["category"],
            unit=candidate["unit"],
            book_qty=to_decimal(candidate["bookQty"]),
            raw={"goodsName": candidate.get("goodsName", "")},
        ))
    db.commit()
    db.refresh(task)
    return task


def _task_rows(db: Session, task_id: int) -> list[InventoryStocktakeItem]:
    return (
        db.query(InventoryStocktakeItem)
        .filter(InventoryStocktakeItem.task_id == task_id)
        .order_by(InventoryStocktakeItem.item_kind, InventoryStocktakeItem.code, InventoryStocktakeItem.id)
        .all()
    )


def _status_label(status: str) -> str:
    return {
        "pending": "待盘点",
        "counting": "盘点中",
        "review": "待确认",
        "completed": "已完成",
        "cancelled": "已取消",
    }.get(status, status)


def serialize_task(db: Session, task: InventoryStocktakeTask, *, include_items: bool = False) -> dict:
    warehouse = db.get(Warehouse, task.warehouse_id)
    rows = _task_rows(db, task.id)
    counted = sum(1 for row in rows if row.actual_qty is not None)
    diff_rows = [row for row in rows if row.difference_qty is not None and to_decimal(row.difference_qty) != 0]
    payload = {
        "id": task.id,
        "number": task.number,
        "scope": task.scope,
        "scopeLabel": "全部盘点" if task.scope == "all" else "部分盘点",
        "status": task.status,
        "statusLabel": _status_label(task.status),
        "warehouseId": task.warehouse_id,
        "warehouseName": warehouse.name if warehouse else "",
        "itemKinds": list(task.item_kinds or []),
        "searchText": task.search_text or "",
        "categoryFilter": task.category_filter or "",
        "note": task.note or "",
        "createdBy": task.created_by or "",
        "confirmedBy": task.confirmed_by or "",
        "confirmedAt": task.confirmed_at.isoformat() if task.confirmed_at else None,
        "createdAt": task.created_at.isoformat() if task.created_at else None,
        "updatedAt": task.updated_at.isoformat() if task.updated_at else None,
        "itemCount": len(rows),
        "countedCount": counted,
        "differenceCount": len(diff_rows),
        "differenceAbsQty": _qty(sum((abs(to_decimal(row.difference_qty)) for row in diff_rows), Decimal("0"))),
    }
    if include_items:
        payload["items"] = [{
            "id": row.id,
            "kind": row.item_kind,
            "refId": row.ref_id,
            "code": row.code,
            "name": row.name,
            "goodsName": (row.raw or {}).get("goodsName", ""),
            "category": row.category or "",
            "unit": row.unit or "",
            "bookQty": _qty(row.book_qty),
            "actualQty": _qty(row.actual_qty) if row.actual_qty is not None else None,
            "differenceQty": _qty(row.difference_qty) if row.difference_qty is not None else None,
            "reason": row.reason or "",
        } for row in rows]
    return payload


def list_tasks(db: Session, *, status: str = "", limit: int = 100) -> list[dict]:
    query = db.query(InventoryStocktakeTask)
    if status.strip():
        query = query.filter(InventoryStocktakeTask.status == status.strip())
    rows = query.order_by(InventoryStocktakeTask.id.desc()).limit(max(1, min(limit, 300))).all()
    return [serialize_task(db, row, include_items=False) for row in rows]


def task_detail(db: Session, task_id: int) -> dict:
    task = db.get(InventoryStocktakeTask, task_id)
    if task is None:
        raise ValueError("盘点任务不存在")
    return serialize_task(db, task, include_items=True)


def save_counts(db: Session, task_id: int, items: list[dict]) -> InventoryStocktakeTask:
    task = db.get(InventoryStocktakeTask, task_id)
    if task is None:
        raise ValueError("盘点任务不存在")
    if task.status not in OPEN_STATUSES:
        raise ValueError("当前盘点任务已结束，不能再修改实盘数")

    rows = {row.id: row for row in _task_rows(db, task.id)}
    touched = 0
    for payload in items:
        row = rows.get(int(payload.get("id") or 0))
        if row is None:
            raise ValueError("提交的盘点明细不属于当前任务")
        raw_actual = payload.get("actual_qty")
        if raw_actual in (None, ""):
            row.actual_qty = None
            row.difference_qty = None
        else:
            actual = to_decimal(raw_actual)
            if not actual.is_finite() or actual < 0:
                raise ValueError(f"{row.code} 的实盘数量必须是大于等于 0 的有效数字")
            row.actual_qty = actual
            row.difference_qty = actual - to_decimal(row.book_qty)
        if "reason" in payload:
            row.reason = str(payload.get("reason") or "").strip()
        touched += 1

    all_rows = list(rows.values())
    counted = sum(1 for row in all_rows if row.actual_qty is not None)
    if counted == 0:
        task.status = "pending"
    elif counted == len(all_rows):
        task.status = "review"
    else:
        task.status = "counting"
    if touched:
        db.commit()
        db.refresh(task)
    return task


def _current_book_map(db: Session, task: InventoryStocktakeTask) -> dict[tuple[str, int], Decimal]:
    candidates = stocktake_candidates(
        db,
        warehouse_id=task.warehouse_id,
        item_kinds=list(task.item_kinds or []),
    )
    return {
        (item["kind"], int(item["id"])): to_decimal(item["bookQty"])
        for item in candidates
    }


def confirm_task(db: Session, task_id: int, *, actor: str = "system") -> InventoryStocktakeTask:
    task = db.get(InventoryStocktakeTask, task_id)
    if task is None:
        raise ValueError("盘点任务不存在")
    if task.status == "completed":
        return task
    if task.status not in OPEN_STATUSES:
        raise ValueError("当前盘点任务不能确认")

    rows = _task_rows(db, task.id)
    if not rows or any(row.actual_qty is None for row in rows):
        raise ValueError("还有未填写实盘数量的明细，不能确认")
    missing_reason = [
        row.code for row in rows
        if to_decimal(row.difference_qty) != 0 and not (row.reason or "").strip()
    ]
    if missing_reason:
        raise ValueError(f"存在差异的项目必须填写原因：{'、'.join(missing_reason[:8])}")

    current = _current_book_map(db, task)
    changed = []
    for row in rows:
        live = current.get((row.item_kind, row.ref_id))
        if live is None or live != to_decimal(row.book_qty):
            changed.append(row.code)
    if changed:
        raise ValueError(
            "盘点期间库存发生了新的出入库变化，请重新建立盘点任务后再确认："
            + "、".join(changed[:8])
        )

    warehouse = _warehouse(db, task.warehouse_id)
    for row in rows:
        diff = to_decimal(row.difference_qty)
        if row.item_kind != "consumable" or diff == 0:
            continue
        consumable_service.record_transaction(
            db,
            consumable_id=row.ref_id,
            transaction_type="stocktake",
            quantity=str(diff),
            source_type="stocktake",
            source_id=row.id,
            note=f"{task.number} · {(row.reason or '盘点差异').strip()}",
            warehouse_id=warehouse.id,
            commit=False,
        )

    task.status = "completed"
    task.confirmed_by = actor
    task.confirmed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(task)
    return task


def cancel_task(db: Session, task_id: int) -> InventoryStocktakeTask:
    task = db.get(InventoryStocktakeTask, task_id)
    if task is None:
        raise ValueError("盘点任务不存在")
    if task.status == "completed":
        raise ValueError("已完成盘点不能取消")
    task.status = "cancelled"
    db.commit()
    db.refresh(task)
    return task
