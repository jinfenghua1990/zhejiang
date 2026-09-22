from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin


class IntegrationConnection(Base, PkMixin, TimestampMixin):
    """外部系统连接状态。未配置显示 unconfigured，禁止伪装已连接。"""

    __tablename__ = "integration_connections"
    __table_args__ = (UniqueConstraint("provider", "mode", name="uq_integration_provider_mode"),)

    provider: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    mode: Mapped[str] = mapped_column(String(64), default="")  # mcp / openapi / oauth / file / smtp
    status: Mapped[str] = mapped_column(String(32), default="unconfigured")  # unconfigured/transport_connected/connected/blocked/error/available
    phase: Mapped[int] = mapped_column(Integer, default=1)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_summary: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)


class IntegrationCredential(Base, PkMixin, TimestampMixin):
    """凭证加密落库；不回传前端。"""

    __tablename__ = "integration_credentials"

    provider: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    key_hint: Mapped[str] = mapped_column(String(128), default="")  # 例如 AppKey / 账号后四位
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SyncJob(Base, PkMixin, TimestampMixin):
    __tablename__ = "sync_jobs"
    __table_args__ = (
        Index(
            "uq_jky_order_sync_running",
            "provider",
            "job_type",
            unique=True,
            postgresql_where=text("provider = 'jky_order' AND job_type = 'orders' AND status = 'running'"),
        ),
    )

    provider: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running")  # running/success/failed/skipped
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_summary: Mapped[str] = mapped_column(Text, default="")


class SyncCheckpoint(Base, PkMixin, TimestampMixin):
    """增量同步断点。"""

    __tablename__ = "sync_checkpoints"
    __table_args__ = (UniqueConstraint("provider", "job_type", name="uq_checkpoint_provider_job"),)

    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    checkpoint: Mapped[dict] = mapped_column(JSONB, default=dict)


class SyncLog(Base, PkMixin):
    __tablename__ = "sync_logs"

    sync_job_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")  # info/warn/error
    message: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSONB, default=dict)


class RawApiPayload(Base, PkMixin):
    """第一次请求保留 raw payload（规格 5）。"""

    __tablename__ = "raw_api_payloads"
    __table_args__ = (UniqueConstraint("provider", "request_digest", name="uq_raw_payload_provider_digest"),)

    provider: Mapped[str] = mapped_column(String(64), index=True)
    method: Mapped[str] = mapped_column(String(128), default="")
    request_digest: Mapped[str] = mapped_column(String(64), default="", index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")
