from sqlalchemy import BigInteger, Boolean, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, PkMixin, TimestampMixin


class User(Base, PkMixin, TimestampMixin):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # RBAC：pbkdf2_sha256 散列，登录后签发 HMAC 令牌（见 core/auth.py）
    hashed_password: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # 每次改密、退出或强制重置密码时递增，使此前签发的长期令牌立即失效。
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    # user_roles 表无外键约束，显式指定 join 条件
    roles: Mapped[list["Role"]] = relationship(
        secondary="user_roles",
        primaryjoin="User.id == UserRole.user_id",
        secondaryjoin="Role.id == UserRole.role_id",
        lazy="selectin",
        viewonly=True,
    )


class Role(Base, PkMixin, TimestampMixin):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), default="")


class UserRole(Base, PkMixin):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_user_role"),)

    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    role_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    # 保留 RBAC 结构；局域网信任模式下不启用（见 IMPLEMENTATION_STATUS）


class AuditLog(Base, PkMixin):
    __tablename__ = "audit_logs"

    actor: Mapped[str] = mapped_column(String(64), default="system", index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), default="", index=True)
    object_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    seq: Mapped[int] = mapped_column(Integer, default=0)
