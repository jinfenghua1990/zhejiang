"""全局鉴权依赖：所有 /api/v1 接口默认需要登录令牌。

- 令牌来源：仅接受 Authorization: Bearer <token>，避免令牌进入 URL/日志/历史记录
- 豁免路径：登录接口本身、1688 OAuth 回调（外部重定向无法携带请求头）
- 角色校验：require_roles("admin", ...) 供敏感接口做 RBAC
"""

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import decode_token
from app.config import settings
from app.db import get_db
from app.models.org import User

# 无需登录即可访问（登录本身 + 1688 授权回调跳转）
_PUBLIC_PATHS = {"/api/v1/auth/login", "/api/v1/auth/config"}
_PUBLIC_PREFIXES = ("/api/v1/integrations/alibaba1688/callback",)
_SELF_SERVICE_WRITE_PATHS = {"/api/v1/auth/change-password", "/api/v1/auth/logout"}


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def _open_access_user(db: Session) -> User | None:
    """直达模式使用现有启用账号作为本地审计身份，不创建新账号。"""
    user = db.scalar(
        select(User).where(
            User.username == settings.ADMIN_USERNAME,
            User.is_active.is_(True),
        )
    )
    return user or db.scalar(select(User).where(User.is_active.is_(True)).order_by(User.id))


async def require_auth(request: Request, db: Session = Depends(get_db)) -> User | None:
    """api_router 级依赖：校验令牌并加载用户；豁免路径直接放行。"""
    path = request.url.path
    if path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES):
        return None

    if settings.ACCESS_MODE == "open":
        user = _open_access_user(db)
        request.state.username = user.username if user is not None else "local"
        return user

    token = _extract_token(request)
    payload = decode_token(token) if token else None
    if not payload:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")

    try:
        uid = int(payload.get("uid") or 0)
        token_version = int(payload.get("ver", -1))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="未登录或登录已过期")

    user = db.get(User, uid)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="账号不存在或已停用")
    if token_version != user.token_version:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")

    # viewer 只读；admin/operator 可执行写操作。改自己密码和退出对所有账号开放。
    if (
        request.method not in {"GET", "HEAD", "OPTIONS"}
        and path not in _SELF_SERVICE_WRITE_PATHS
    ):
        role_codes = {role.code for role in user.roles}
        if not role_codes.intersection({"admin", "operator"}):
            raise HTTPException(status_code=403, detail="当前账号仅有查看权限")

    # 后续业务审计可直接取 request.state.username
    request.state.username = user.username
    return user


def current_actor(request: Request) -> str:
    """返回已通过全局鉴权的用户名，供业务审计使用。"""
    return getattr(request.state, "username", "system")


def require_roles(*role_codes: str):
    """RBAC 依赖工厂：仅允许持有指定角色之一的用户访问。"""

    def checker(user: User = Depends(require_auth)) -> User:
        if settings.ACCESS_MODE == "open":
            if user is None:
                raise HTTPException(status_code=503, detail="直达模式缺少本地启用账号")
            return user
        if user is None:  # 豁免路径上不会挂本依赖，防御性兜底
            raise HTTPException(status_code=401, detail="未登录")
        codes = {r.code for r in user.roles}
        if not codes.intersection(role_codes):
            raise HTTPException(status_code=403, detail="权限不足")
        return user

    return checker
