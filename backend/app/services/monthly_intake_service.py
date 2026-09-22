"""月度业务资料入库：把固定的两份吉客云源文件和导入结果绑定到账期。

``ArchiveFile`` 负责保存每次上传的原件版本；本服务负责把当前账期的
「采购入库单 / 销售单查询」送入现有业务导入器，并持久化处理结果。这样
页面刷新或下个月回来时，不能只凭“文件存在”误判为已经入库。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.finance import ArchiveFile, MonthlyIntakeSource
from app.services import finance_service


SOURCE_DEFINITIONS: dict[str, dict[str, str]] = {
    "purchase_inbound": {
        "label": "采购入库单",
        "category": "purchase_inbound",
        "hint": "吉客云入库申请单货品 / 采购入库明细，用于入库数量、金额、成本和采购链路。",
    },
    "sales_query": {
        "label": "销售单查询",
        "category": "sales_query",
        "hint": "吉客云销售单查询（销售单 + 销售单货品），用于销售数量和收入维度。",
    },
}

READY_STATUS = "IMPORTED"


def validate_source_type(source_type: str) -> dict[str, str]:
    definition = SOURCE_DEFINITIONS.get(source_type)
    if definition is None:
        raise ValueError("source_type 只能是 purchase_inbound 或 sales_query")
    return definition


def _serialize_archive(row: ArchiveFile | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "category": row.category,
        "originalName": row.original_name,
        "size": row.size,
        "sha256": row.sha256[:16],
        "version": row.version,
        "uploader": row.uploader,
        "uploadedAt": row.uploaded_at.isoformat() if row.uploaded_at else None,
    }


def _latest_archive(
    db: Session, *, company: str, year: int, month: int, category: str
) -> ArchiveFile | None:
    return (
        db.query(ArchiveFile)
        .filter_by(
            company=company,
            period_year=year,
            period_month=month,
            category=category,
        )
        .order_by(ArchiveFile.version.desc(), ArchiveFile.id.desc())
        .first()
    )


def _source_status(
    db: Session,
    *,
    company: str,
    year: int,
    month: int,
    source_type: str,
) -> dict[str, Any]:
    definition = validate_source_type(source_type)
    row = (
        db.query(MonthlyIntakeSource)
        .filter_by(
            company=company,
            period_year=year,
            period_month=month,
            source_type=source_type,
        )
        .first()
    )
    archive = db.get(ArchiveFile, row.archive_file_id) if row and row.archive_file_id else None
    if archive is None:
        archive = _latest_archive(
            db,
            company=company,
            year=year,
            month=month,
            category=definition["category"],
        )

    if row is None:
        status = "UPLOADED" if archive else "MISSING"
        stats: dict[str, Any] = {}
        error = ""
        imported_at = None
        report_type = ""
    else:
        status = row.status or "MISSING"
        stats = row.stats or {}
        error = row.error_summary or ""
        imported_at = row.imported_at.isoformat() if row.imported_at else None
        report_type = row.report_type or ""
        if archive is None:
            status = "MISSING"
            error = "当前版本原始文件已不存在，请重新上传。"

    return {
        "sourceType": source_type,
        "label": definition["label"],
        "hint": definition["hint"],
        "required": True,
        "status": status,
        "reportType": report_type,
        "stats": stats,
        "error": error,
        "importedAt": imported_at,
        "archiveFile": _serialize_archive(archive),
    }


def status(
    db: Session, *, company: str, year: int, month: int
) -> dict[str, Any]:
    finance_service.validate_period(year, month)
    sources = [
        _source_status(
            db,
            company=company,
            year=year,
            month=month,
            source_type=source_type,
        )
        for source_type in SOURCE_DEFINITIONS
    ]
    ready = all(item["status"] == READY_STATUS for item in sources)
    return {
        "company": company,
        "year": year,
        "month": month,
        "sourceReady": ready,
        "requiredCount": len(sources),
        "readyCount": sum(item["status"] == READY_STATUS for item in sources),
        "sources": sources,
    }


def _save_result(
    db: Session,
    *,
    company: str,
    year: int,
    month: int,
    source_type: str,
    archive: ArchiveFile,
    status_value: str,
    report_type: str,
    stats: dict[str, Any],
    error_summary: str,
    imported_at: datetime | None,
) -> MonthlyIntakeSource:
    row = (
        db.query(MonthlyIntakeSource)
        .filter_by(
            company=company,
            period_year=year,
            period_month=month,
            source_type=source_type,
        )
        .first()
    )
    if row is None:
        row = MonthlyIntakeSource(
            company=company,
            period_year=year,
            period_month=month,
            source_type=source_type,
        )
        db.add(row)
    row.archive_file_id = archive.id
    row.sha256 = archive.sha256
    row.status = status_value
    row.report_type = report_type
    row.stats = stats
    row.error_summary = error_summary[:4000]
    row.imported_at = imported_at
    db.commit()
    return row


def _mark_error(
    db: Session,
    *,
    company: str,
    year: int,
    month: int,
    source_type: str,
    archive: ArchiveFile,
    error: str,
) -> None:
    # 调用方可能刚经历了导入器 rollback；重新从当前 Session 取干净状态。
    db.rollback()
    _save_result(
        db,
        company=company,
        year=year,
        month=month,
        source_type=source_type,
        archive=archive,
        status_value="ERROR",
        report_type="",
        stats={},
        error_summary=error,
        imported_at=None,
    )


def ingest(
    db: Session,
    *,
    company: str,
    year: int,
    month: int,
    source_type: str,
    content: bytes,
    original_name: str,
    actor: str,
) -> dict[str, Any]:
    """归档并导入一个月度源文件；导入失败也保留原件并记录 ERROR。"""
    definition = validate_source_type(source_type)
    finance_service.validate_period(year, month)
    if not content:
        raise ValueError("空文件")

    archive = finance_service.store_upload(
        db,
        company=company,
        year=year,
        month=month,
        category=definition["category"],
        original_name=original_name or f"{definition['label']}.xlsx",
        content=content,
        actor=actor,
    )

    try:
        if source_type == "sales_query":
            from app.services import sales_file_import_service

            import_result = sales_file_import_service.import_sales_file(
                db,
                content=content,
                original_name=original_name or "销售单查询.xlsx",
                actor=actor,
            )
            report_type = "sales"
            stats = {"import": import_result}
            imported = int(import_result.get("ordersImported") or 0) > 0
            if not imported:
                status_value = "REVIEW"
                error_summary = "文件已解析，但没有可导入的有效销售订单，请核对账期和订单状态。"
            else:
                status_value = READY_STATUS
                error_summary = ""
        else:
            from app.services import jackyun_file_import_service

            imported_row, duplicate = jackyun_file_import_service.import_export(
                db,
                content=content,
                original_name=original_name or "采购入库单.xlsx",
                actor=actor,
                auto_confirm=True,
            )
            map_result = jackyun_file_import_service.map_import(
                db, imported_row.id, actor=actor
            )
            from app.services.procurement_chain_service import run_full_procurement_automation

            automation = run_full_procurement_automation(db, actor=actor)
            stats = {
                "duplicate": duplicate,
                "import": jackyun_file_import_service.serialize_import(imported_row),
                "mapping": map_result,
                "automation": {
                    "ok": bool(automation.get("ok", True)),
                    "errors": automation.get("errors", []),
                },
            }
            report_type = imported_row.report_type or ""
            if map_result.get("skipped"):
                status_value = "REVIEW"
                error_summary = map_result.get("reason") or "文件已归档，但尚未完成入库明细映射。"
            elif not map_result.get("ok", False):
                status_value = "ERROR"
                error_summary = map_result.get("error") or "采购入库文件映射失败。"
            else:
                status_value = READY_STATUS
                error_summary = "；".join(automation.get("errors") or [])
                if error_summary:
                    # 采购数据已经入库；自动链路有告警时要求人工复核，但不把已导入事实伪装成失败。
                    status_value = "REVIEW"

        row = _save_result(
            db,
            company=company,
            year=year,
            month=month,
            source_type=source_type,
            archive=archive,
            status_value=status_value,
            report_type=report_type,
            stats=stats,
            error_summary=error_summary,
            imported_at=datetime.now(timezone.utc),
        )
    except Exception as exc:
        _mark_error(
            db,
            company=company,
            year=year,
            month=month,
            source_type=source_type,
            archive=archive,
            error=str(exc),
        )
        return {
            "ok": False,
            "sourceType": source_type,
            "status": "ERROR",
            "error": str(exc),
            "archiveFile": _serialize_archive(archive),
        }

    return {
        "ok": row.status == READY_STATUS,
        "sourceType": source_type,
        "status": row.status,
        "reportType": row.report_type,
        "stats": row.stats or {},
        "error": row.error_summary or "",
        "archiveFile": _serialize_archive(archive),
    }


def require_ready(db: Session, *, company: str, year: int, month: int) -> None:
    """发送前强制检查两份业务源文件已经真正导入。"""
    current = status(db, company=company, year=year, month=month)
    if current["sourceReady"]:
        return
    missing = [
        f"{item['label']}：{item['error'] or '待上传或待校验'}"
        for item in current["sources"]
        if item["status"] != READY_STATUS
    ]
    raise ValueError("月度资料入库未完成：" + "；".join(missing))
