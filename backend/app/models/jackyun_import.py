"""吉客云客户端导出文件的受管导入记录。

这不是对吉客云私有接口的替代：仅保存用户从客户端官方导出的文件，
并记录解析后的原始行，等待基于真实表头的字段映射。
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin


class JackyunFileImport(Base, PkMixin, TimestampMixin):
    __tablename__ = "jackyun_file_imports"
    __table_args__ = (
        UniqueConstraint("sha256", name="uq_jackyun_file_import_sha256"),
    )

    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    mime: Mapped[str] = mapped_column(String(128), default="")
    report_type: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    status: Mapped[str] = mapped_column(String(32), default="parsed", index=True)
    sheet_name: Mapped[str] = mapped_column(String(256), default="")
    headers: Mapped[list] = mapped_column(JSONB, default=list)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    staged_row_count: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str] = mapped_column(Text, default="")
    uploader: Mapped[str] = mapped_column(String(64), default="system")
    # 导入生命周期：draft=解析完毕待人工确认，active=确认后出现在业务页，deleted=软删除（可恢复）。
    lifecycle: Mapped[str] = mapped_column(String(16), default="active", index=True)
    lifecycle_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JackyunFileImportRecord(Base, PkMixin):
    __tablename__ = "jackyun_file_import_records"
    __table_args__ = (
        UniqueConstraint("import_id", "row_index", name="uq_jackyun_file_import_record"),
    )

    import_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    # 行级状态：active=保留；deleted=用户在明细核对中删除的行（可恢复，不参与后续映射）。
    row_status: Mapped[str] = mapped_column(String(16), default="active", server_default="active", index=True)
