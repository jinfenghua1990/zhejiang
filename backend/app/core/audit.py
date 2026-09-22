from typing import Any

from sqlalchemy.orm import Session

from app.models.org import AuditLog


def audit(
    db: Session,
    actor: str,
    action: str,
    object_type: str = "",
    object_id: str = "",
    detail: dict[str, Any] | None = None,
    *,
    commit: bool = True,
) -> None:
    """所有调整/状态变更必须写审计日志；commit=False 时由上层统一提交/回滚。"""
    db.add(
        AuditLog(
            actor=actor or "system",
            action=action,
            object_type=object_type,
            object_id=str(object_id or ""),
            detail=detail or {},
        )
    )
    if commit:
        db.commit()
