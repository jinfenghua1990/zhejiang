"""认证与用户管理：登录 / 当前用户 / 改密码 / 用户管理（admin）。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_auth, require_roles
from app.core.audit import audit
from app.core.auth import create_token, hash_password, password_needs_rehash, verify_password

_TOKEN_TTL_DEFAULT = 12 * 3600  # 12 小时
_TOKEN_TTL_REMEMBER = 30 * 24 * 3600  # 记住登录：30 天
from app.db import get_db
from app.models.org import Role, User, UserRole

router = APIRouter(prefix="/auth", tags=["认证"])


class LoginBody(BaseModel):
    username: str
    password: str
    # 勾选“记住登录”签发 30 天令牌（同一浏览器免重复输入）；默认 12 小时
    remember_me: bool = False


class ChangePasswordBody(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=64)


class UserCreateBody(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=64)
    display_name: str = Field(default="", max_length=128)
    roles: list[str] = Field(default_factory=lambda: ["viewer"])


def _user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "displayName": user.display_name,
        "roles": sorted(r.code for r in user.roles),
        "isActive": user.is_active,
    }


@router.get("/config")
def auth_config() -> dict:
    """前端运行时读取真实鉴权模式，避免静态构建时环境变量与后端不一致。"""
    from app.config import settings

    return {"accessMode": settings.ACCESS_MODE}


@router.post("/login")
def login(body: LoginBody, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not verify_password(body.password, user.hashed_password):
        audit(db, body.username, "LOGIN_FAILED", detail={"reason": "用户名或密码错误"})
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        audit(db, user.username, "LOGIN_FAILED", detail={"reason": "账号已停用"})
        raise HTTPException(status_code=403, detail="账号已停用")

    # 旧 PBKDF2 工作因子在成功登录时平滑升级；不要求用户主动改密码。
    # audit() 与当前 session 同事务提交，因此 hash 更新和登录审计一起持久化。
    if password_needs_rehash(user.hashed_password):
        user.hashed_password = hash_password(body.password)
        db.add(user)

    ttl = _TOKEN_TTL_REMEMBER if body.remember_me else _TOKEN_TTL_DEFAULT
    token = create_token(
        uid=user.id,
        username=user.username,
        token_version=user.token_version,
        ttl_seconds=ttl,
    )
    audit(db, user.username, "LOGIN_SUCCESS", detail={"rememberMe": body.remember_me})
    return {"accessToken": token, "user": _user_payload(user)}


@router.get("/me")
def me(user: User = Depends(require_auth)) -> dict:
    if user is None:
        raise HTTPException(status_code=401, detail="未登录")
    return _user_payload(user)


@router.post("/change-password")
def change_password(
    body: ChangePasswordBody,
    user: User = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict:
    if user is None:
        raise HTTPException(status_code=401, detail="未登录")
    if not verify_password(body.old_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="原密码不正确")
    user.hashed_password = hash_password(body.new_password)
    user.token_version += 1
    db.add(user)
    db.commit()
    audit(db, user.username, "PASSWORD_CHANGED", object_type="user", object_id=str(user.id))
    return {"ok": True}


@router.post("/logout")
def logout(
    user: User = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict:
    """退出并撤销该账号此前签发的全部令牌。"""
    if user is None:
        raise HTTPException(status_code=401, detail="未登录")
    user.token_version += 1
    db.add(user)
    db.commit()
    audit(db, user.username, "LOGOUT", object_type="user", object_id=str(user.id))
    return {"ok": True}


@router.get("/users")
def list_users(
    _admin: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> list[dict]:
    users = db.scalars(select(User).order_by(User.id)).all()
    return [_user_payload(u) for u in users]


@router.post("/users")
def create_user(
    body: UserCreateBody,
    admin: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict:
    if db.scalar(select(User).where(User.username == body.username)):
        raise HTTPException(status_code=409, detail="用户名已存在")

    codes = list(dict.fromkeys(body.roles))
    roles = db.scalars(select(Role).where(Role.code.in_(codes))).all()
    missing = set(codes) - {r.code for r in roles}
    if missing:
        raise HTTPException(status_code=400, detail=f"角色不存在: {', '.join(sorted(missing))}")

    user = User(
        username=body.username,
        display_name=body.display_name,
        hashed_password=hash_password(body.password),
    )
    db.add(user)
    db.flush()
    for role in roles:
        db.add(UserRole(user_id=user.id, role_id=role.id))
    db.commit()
    audit(
        db,
        admin.username,
        "USER_CREATED",
        object_type="user",
        object_id=str(user.id),
        detail={"username": user.username, "roles": sorted(r.code for r in roles)},
    )
    return _user_payload(user)
