"""幂等种子：内置角色 + 初始管理员（ADMIN_USERNAME / ADMIN_PASSWORD）。

Docker 启动时在 alembic 迁移后执行（见 backend/Dockerfile CMD）。
- 角色不存在则创建：admin / operator / viewer
- 管理员不存在则创建并赋予 admin 角色
- 已存在则不覆盖密码（除非 FORCE_ADMIN_PASSWORD=1），避免重启重置已改过的密码
"""

from sqlalchemy import select

from app.config import settings
from app.core.auth import hash_password
from app.db import SessionLocal
from app.models.finance import FinanceLegalEntity
from app.models.org import Role, User, UserRole
from app.services.finance_center_service import DEFAULT_ENTITY_CODE, DEFAULT_ENTITY_NAME

BUILTIN_ROLES = [
    ("admin", "管理员"),
    ("operator", "操作员"),
    ("viewer", "查看者"),
]


def ensure_seed(db) -> dict:
    result: dict = {"adminCreated": False, "passwordSet": False, "rulesSeeded": False}

    for code, name in BUILTIN_ROLES:
        if db.scalar(select(Role).where(Role.code == code)) is None:
            db.add(Role(code=code, name=name))
    db.flush()

    admin_role = db.scalar(select(Role).where(Role.code == "admin"))
    admin = db.scalar(select(User).where(User.username == settings.ADMIN_USERNAME))

    if admin is None:
        admin = User(
            username=settings.ADMIN_USERNAME,
            display_name="管理员",
            hashed_password=hash_password(settings.ADMIN_PASSWORD)
            if settings.ADMIN_PASSWORD
            else None,
        )
        db.add(admin)
        db.flush()
        db.add(UserRole(user_id=admin.id, role_id=admin_role.id))
        result["adminCreated"] = True
        result["passwordSet"] = bool(settings.ADMIN_PASSWORD)
    else:
        # 关联可能缺失（老数据），补挂 admin 角色
        linked = db.scalar(
            select(UserRole).where(
                UserRole.user_id == admin.id, UserRole.role_id == admin_role.id
            )
        )
        if linked is None:
            db.add(UserRole(user_id=admin.id, role_id=admin_role.id))
        # 仅在无密码或强制重置时写入
        if settings.ADMIN_PASSWORD and (
            settings.FORCE_ADMIN_PASSWORD or not admin.hashed_password
        ):
            admin.hashed_password = hash_password(settings.ADMIN_PASSWORD)
            admin.token_version += 1
            result["passwordSet"] = True

    db.commit()
    # 默认财务主体：启动时幂等创建，避免 GET 财务中心在只读路径上写库。
    if (
        db.scalar(
            select(FinanceLegalEntity).where(FinanceLegalEntity.code == DEFAULT_ENTITY_CODE)
        )
        is None
    ):
        db.add(
            FinanceLegalEntity(
                code=DEFAULT_ENTITY_CODE,
                name=DEFAULT_ENTITY_NAME,
                country_code="CN",
                base_currency="CNY",
                status="active",
                is_default=True,
                business_scopes=["domestic", "foreign_trade"],
                note="系统初始化默认主体",
            )
        )
        db.commit()
    # 默认对方户名规则是系统初始数据，不能在 GET /rules、GET /suggestions 中懒写入。
    from app.services.reconciliation import seed_rules_if_empty

    result["rulesSeeded"] = seed_rules_if_empty(db)
    return result


def main() -> None:
    with SessionLocal() as db:
        result = ensure_seed(db)
    if result["adminCreated"]:
        print(f"[seed] 已创建管理员 {settings.ADMIN_USERNAME}")
    if result["passwordSet"]:
        print(f"[seed] {settings.ADMIN_USERNAME} 密码已设置")
    if not result["adminCreated"] and not result["passwordSet"]:
        print("[seed] 无变更（幂等跳过）")
    if not settings.ADMIN_PASSWORD:
        print("[seed] 警告：ADMIN_PASSWORD 未配置，无法用于登录")


if __name__ == "__main__":
    main()
