"""数据中心导入生命周期（draft / active / deleted）的共享管理。

业务查询通过 JOIN 导入表 lifecycle 列过滤草稿与已删除数据，
所以状态切换只更新导入行本身，不级联子表。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, TypeVar

from sqlalchemy.orm import Session

from app.core.audit import audit

ModelT = TypeVar("ModelT")

VALID_LIFECYCLES = ("draft", "active", "deleted")


class LifecycleTransitionError(ValueError):
    """请求的 lifecycle 切换不合法（不在允许的 from_states 内）。"""

    def __init__(self, current: str, requested: str, allowed_from: Iterable[str]) -> None:
        super().__init__(
            f"当前状态 {current!r} 不允许切换到 {requested!r}（仅允许从 {sorted(allowed_from)} 切换）"
        )
        self.current = current
        self.requested = requested


def transition_lifecycle(
    db: Session,
    model: type[ModelT],
    import_id: int,
    *,
    target: str,
    allowed_from: Iterable[str],
    actor: str,
    audit_action: str,
) -> ModelT:
    """把导入行 lifecycle 切到 ``target``；幂等；不合法状态会抛 LifecycleTransitionError。"""
    if target not in VALID_LIFECYCLES:
        raise ValueError(f"非法 lifecycle 目标: {target}")
    allowed_set = set(allowed_from)
    row = db.get(model, import_id)
    if row is None:
        raise LookupError(f"{model.__tablename__} #{import_id} 不存在")
    if row.lifecycle == target:
        return row  # 幂等
    if row.lifecycle not in allowed_set:
        raise LifecycleTransitionError(row.lifecycle, target, allowed_set)
    previous = row.lifecycle
    row.lifecycle = target
    row.lifecycle_changed_at = datetime.now(timezone.utc)
    db.commit()
    audit(
        db,
        actor,
        audit_action,
        model.__tablename__,
        import_id,
        {"previous": previous, "current": target},
    )
    db.refresh(row)
    return row


def filter_lifecycle(query, model: type[ModelT], lifecycle: str | None):
    """按 lifecycle 过滤；None / 空字符串表示不过滤。"""
    if lifecycle:
        return query.filter(model.lifecycle == lifecycle)
    return query


def filter_active_import(query, child_model, import_model, fk_column):
    """子表行通过 ``fk_column`` 关联到导入表 ``import_model``；只保留 active 导入带来的行。

    ``fk_column`` 为 NULL（如改造前遗留的税务发票）时保留，避免历史数据被误隐藏。
    """
    return (
        query.join(import_model, fk_column == import_model.id, isouter=True)
        .filter((fk_column.is_(None)) | (import_model.lifecycle == "active"))
    )


ROW_STATUSES = ("active", "deleted")


def transition_row_status(db: Session, model: type[ModelT], *, lookup: dict, target: str,
                          actor: str, audit_action: str) -> ModelT:
    """行级软删除 / 恢复；幂等；行不存在抛 LookupError。

    ``lookup`` 是唯一定位条件，如 ``{"import_id": ..., "row_index": ...}``
    或 ``{"import_id": ..., "id": ...}``。
    """
    if target not in ROW_STATUSES:
        raise ValueError(f"非法 row_status 目标: {target}")
    row = db.query(model).filter_by(**lookup).first()
    if row is None:
        raise LookupError(f"{model.__tablename__} {lookup} 不存在")
    if row.row_status == target:
        return row  # 幂等
    previous = row.row_status
    row.row_status = target
    db.commit()
    audit(db, actor, audit_action, model.__tablename__, row.id, {"previous": previous, "current": target})
    db.refresh(row)
    return row


def filter_active_rows(query, row_model: type[ModelT]):
    """业务查询只保留 ``row_status='active'`` 的行（迁移回填后不会有 NULL）。"""
    return query.filter(row_model.row_status == "active")