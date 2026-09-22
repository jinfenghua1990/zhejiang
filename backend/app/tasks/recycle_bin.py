"""回收站定时清理：软删除超过保留期的导入及其子表数据硬删除。

保留期来自 ``settings.RECYCLE_BIN_RETENTION_DAYS``（默认 30 天）。
软删除只把导入行 lifecycle 置为 ``deleted`` 并保持可恢复；超过保留期后由本任务
把导入行与对应子表（订单/原始行/发票）一并硬删除，真正腾出空间。

删除口径与业务查询隐藏口径一致：税务发票仅在「最后写入方就是本导入」时才清理，
避免误删被其它 active 导入共享的发票。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.celery_app import celery_app
from app.config import settings
from app.db import SessionLocal
from app.models.alibaba1688_import import Alibaba1688FileImport, Alibaba1688Order
from app.models.jackyun_import import JackyunFileImport, JackyunFileImportRecord
from app.models.procurement_chain import ProcurementChainLink
from app.models.tax import (
    TaxInvoice,
    TaxInvoiceImport,
    TaxInvoiceImportRecord,
    TaxInvoiceLink,
)


def _cutoff() -> datetime:
    days = int(getattr(settings, "RECYCLE_BIN_RETENTION_DAYS", 30))
    return datetime.now(timezone.utc) - timedelta(days=days)


def _expired_import_ids(db, model, cutoff) -> list[int]:
    return [
        r.id
        for r in db.query(model)
        .filter(
            model.lifecycle == "deleted",
            model.lifecycle_changed_at.isnot(None),
            model.lifecycle_changed_at < cutoff,
        )
        .all()
    ]


def _purge_1688(db, cutoff) -> int:
    ids = _expired_import_ids(db, Alibaba1688FileImport, cutoff)
    if not ids:
        return 0
    order_ids = [
        o.id for o in db.query(Alibaba1688Order).filter(Alibaba1688Order.import_id.in_(ids)).all()
    ]
    if order_ids:
        db.query(ProcurementChainLink).filter(
            ProcurementChainLink.order_id.in_(order_ids)
        ).delete(synchronize_session=False)
        db.query(TaxInvoiceLink).filter(
            TaxInvoiceLink.target_type == "alibaba1688_order",
            TaxInvoiceLink.target_id.in_(order_ids),
        ).delete(synchronize_session=False)
        db.query(Alibaba1688Order).filter(
            Alibaba1688Order.import_id.in_(ids)
        ).delete(synchronize_session=False)
    db.query(Alibaba1688FileImport).filter(
        Alibaba1688FileImport.id.in_(ids)
    ).delete(synchronize_session=False)
    return len(ids)


def _purge_jackyun(db, cutoff) -> int:
    ids = _expired_import_ids(db, JackyunFileImport, cutoff)
    if not ids:
        return 0
    db.query(JackyunFileImportRecord).filter(
        JackyunFileImportRecord.import_id.in_(ids)
    ).delete(synchronize_session=False)
    db.query(JackyunFileImport).filter(
        JackyunFileImport.id.in_(ids)
    ).delete(synchronize_session=False)
    return len(ids)


def _purge_tax(db, cutoff) -> int:
    ids = _expired_import_ids(db, TaxInvoiceImport, cutoff)
    if not ids:
        return 0
    # 仅清理「最后写入方就是本导入」的发票（与业务查询隐藏口径一致）
    inv_ids = [i.id for i in db.query(TaxInvoice).filter(TaxInvoice.source_import_id.in_(ids)).all()]
    db.query(TaxInvoiceImportRecord).filter(
        TaxInvoiceImportRecord.import_id.in_(ids)
    ).delete(synchronize_session=False)
    if inv_ids:
        db.query(TaxInvoiceLink).filter(
            TaxInvoiceLink.invoice_id.in_(inv_ids)
        ).delete(synchronize_session=False)
        db.query(TaxInvoice).filter(TaxInvoice.id.in_(inv_ids)).delete(synchronize_session=False)
    db.query(TaxInvoiceImport).filter(TaxInvoiceImport.id.in_(ids)).delete(synchronize_session=False)
    return len(ids)


@celery_app.task(name="tasks.recycle_bin_purge", bind=True, max_retries=2, default_retry_delay=300)
def recycle_bin_purge(self) -> dict:
    """每日清理超过保留期的软删除导入。幂等：仅删除 lifecycle=deleted 且超期的行。"""
    db = SessionLocal()
    try:
        cutoff = _cutoff()
        counts = {
            "alibaba1688": _purge_1688(db, cutoff),
            "jackyun": _purge_jackyun(db, cutoff),
            "tax": _purge_tax(db, cutoff),
        }
        db.commit()
        return {
            "status": "success",
            "cutoff": cutoff.isoformat(),
            "purged": counts,
            "total": sum(counts.values()),
        }
    except Exception as exc:
        db.rollback()
        raise self.retry(exc=exc)
    finally:
        db.close()
