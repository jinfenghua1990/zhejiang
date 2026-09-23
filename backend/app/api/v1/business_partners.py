"""财务中心的统一往来单位档案 API。"""

import os
import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.core.audit import audit
from app.db import get_db
from app.models.business_partner import BusinessPartner
from app.services import business_partner_service as partner_service
from app.services import partner_master_service
from app.services.partner_reference_service import partner_reference_coverage


router = APIRouter(prefix="/finance/partners", tags=["finance-partners"])

# 回填同步完全幂等但并不轻量（全量扫 12 张表）。它只兜底“新事实尽快进档案”，
# 不需要每次打开页面都跑：默认 60 秒内只执行一次，可用环境变量调整，0 关闭节流。
_PARTNER_SYNC_THROTTLE_SECONDS = float(os.getenv("PARTNER_SYNC_THROTTLE_SECONDS", "60"))
_sync_lock = threading.Lock()
_last_sync_at = -float("inf")


class PartnerBankAccountInput(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    bank_name: str = Field(default="", max_length=128)
    account_no: str = Field(default="", max_length=128)
    account_name: str = Field(default="", max_length=256)
    is_primary: bool = False


class PartnerInput(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    name: str = Field(min_length=1, max_length=256)
    roles: list[str] = Field(default_factory=lambda: ["counterparty"])
    tax_no: str = Field(default="", max_length=64)
    contact: str = Field(default="", max_length=256)
    phone: str = Field(default="", max_length=64)
    address: str = Field(default="", max_length=512)
    bank_name: str = Field(default="", max_length=128)
    bank_account_no: str = Field(default="", max_length=128)
    bank_account_name: str = Field(default="", max_length=256)
    former_names: list[str] | None = None
    bank_accounts: list[PartnerBankAccountInput] | None = None
    notes: str = Field(default="", max_length=2000)


class IdentifierInput(BaseModel):
    kind: str = Field(default="alias", max_length=32)
    value: str = Field(min_length=1, max_length=512)


class ReviewClaimInput(BaseModel):
    note: str = Field(default="", max_length=1000)


class DuplicateDecisionInput(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    same: bool
    note: str = Field(default="", max_length=1000)


def _sync_and_commit(db: Session) -> dict[str, int]:
    global _last_sync_at
    now = time.monotonic()
    if now - _last_sync_at < _PARTNER_SYNC_THROTTLE_SECONDS:
        return {}
    with _sync_lock:
        now = time.monotonic()
        if now - _last_sync_at < _PARTNER_SYNC_THROTTLE_SECONDS:
            return {}
        result = partner_service.sync_business_partners(db)
        db.commit()
        _last_sync_at = time.monotonic()
        return result


@router.get("")
def list_partners(
    keyword: str = "",
    role: str = Query("all", pattern="^(all|supplier|customer|counterparty)$"),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    # 页面首次进入即可把历史采购、发票、银行流水回填进统一档案；同步完全幂等。
    _sync_and_commit(db)
    return partner_service.list_partners(
        db, keyword=keyword, role=role, limit=limit, offset=offset
    )


@router.post("/sync")
def sync_partners(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """全量重建统一主体关系。

    V2 不再只生成关联表：采购、发票、银行、入库等业务事实会同时写入直接
    partner FK；原始名称/税号/账号保持不变用于审计。
    """
    actor = current_actor(request)
    result = partner_master_service.rebuild_partner_master(db, actor=actor, run_payment_match=True)
    db.commit()
    audit(
        db,
        actor,
        "business_partner.master_rebuilt",
        "business_partner",
        "",
        {key: value for key, value in result.items() if key not in {"coverage"}},
    )
    return {"ok": True, **result}


@router.get("/coverage")
def partner_master_coverage(db: Session = Depends(get_db)) -> dict[str, Any]:
    return partner_reference_coverage(db)


@router.post("/{partner_id}/recheck")
def recheck_partner_matches(
    partner_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """重新核对当前档案的来源归属，并重跑银行付款 ↔ 发票匹配。

    匹配引擎仍按全局唯一性运行，避免同一张发票/同一笔流水在不同往来单位之间
    被重复占用；人工确认、人工拒绝和拆分关系继续由底层匹配器保护，不会自动搬动。
    """
    partner = db.get(BusinessPartner, partner_id)
    if partner is None or partner.status == "archived":
        raise HTTPException(404, "往来单位不存在")

    before = partner_service.partner_detail(db, partner_id) or {}
    rebuild = partner_master_service.rebuild_partner_master(
        db,
        actor=current_actor(request),
        run_payment_match=True,
    )
    db.commit()
    after = partner_service.partner_detail(db, partner_id) or {}

    after_summary = after.get("summary") or {}
    before_invoice_paid = sum(float(row.get("bankPaidAmount", 0) or 0) for row in before.get("invoices", []))
    after_invoice_paid = sum(float(row.get("bankPaidAmount", 0) or 0) for row in after.get("invoices", []))
    before_match_count = sum(len(row.get("invoices", [])) for row in before.get("payments", []))
    after_match_count = sum(len(row.get("invoices", [])) for row in after.get("payments", []))
    result = {
        "ok": True,
        "partnerId": partner_id,
        "createdLinks": int(rebuild.get("createdLinks", 0)),
        "updatedLinks": int(rebuild.get("updatedLinks", 0)),
        "materializedRefs": int(rebuild.get("materializedRefs", 0)),
        "needsReview": int(after_summary.get("needsReviewCount", 0)),
        "partnerMatchesAdded": max(0, after_match_count - before_match_count),
        "partnerInvoicePaidBefore": before_invoice_paid,
        "partnerInvoicePaidAfter": after_invoice_paid,
        "bankInvoiceMatchesCreated": int(rebuild.get("bankInvoiceMatchesCreated", 0)),
        "bankInvoiceRepaired": int(rebuild.get("bankInvoiceRepaired", 0)),
        "bankInvoiceAmbiguous": int(rebuild.get("bankInvoiceAmbiguous", 0)),
        "detail": after,
    }
    audit(
        db,
        current_actor(request),
        "business_partner.rechecked",
        "business_partner",
        str(partner_id),
        {key: value for key, value in result.items() if key != "detail"},
    )
    return result


@router.post("")
def create_partner(
    payload: PartnerInput, request: Request, db: Session = Depends(get_db)
) -> dict[str, Any]:
    try:
        row = partner_service.create_partner(db, payload.model_dump())
        rebuild = partner_master_service.rebuild_partner_master(
            db,
            actor=current_actor(request),
            run_payment_match=True,
        )
        db.commit()
        audit(
            db,
            current_actor(request),
            "business_partner.created",
            "business_partner",
            str(row.id),
            {
                "name": row.name,
                "roles": row.roles,
                "sync": {
                    "createdLinks": int(rebuild.get("createdLinks", 0)),
                    "updatedLinks": int(rebuild.get("updatedLinks", 0)),
                    "materializedRefs": int(rebuild.get("materializedRefs", 0)),
                    "bankInvoiceMatchesCreated": int(rebuild.get("bankInvoiceMatchesCreated", 0)),
                    "bankInvoiceRepaired": int(rebuild.get("bankInvoiceRepaired", 0)),
                },
            },
        )
        return partner_service.partner_detail(db, row.id) or {}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.get("/{partner_id}")
def get_partner(partner_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    _sync_and_commit(db)
    row = partner_service.partner_detail(db, partner_id)
    if row is None:
        raise HTTPException(404, "往来单位不存在")
    return row


@router.put("/{partner_id}")
def update_partner(
    partner_id: int,
    payload: PartnerInput,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        row = partner_service.update_partner(db, partner_id, payload.model_dump())
        rebuild = partner_master_service.rebuild_partner_master(
            db,
            actor=current_actor(request),
            run_payment_match=True,
        )
        sync = {
            "createdLinks": int(rebuild.get("createdLinks", 0)),
            "updatedLinks": int(rebuild.get("updatedLinks", 0)),
            "materializedRefs": int(rebuild.get("materializedRefs", 0)),
            "bankInvoiceMatchesCreated": int(rebuild.get("bankInvoiceMatchesCreated", 0)),
            "bankInvoiceRepaired": int(rebuild.get("bankInvoiceRepaired", 0)),
        }
        db.commit()
        audit(
            db,
            current_actor(request),
            "business_partner.updated",
            "business_partner",
            str(row.id),
            {"name": row.name, "sync": sync},
        )
        return partner_service.partner_detail(db, row.id) or {}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.post("/{partner_id}/identifiers")
def add_partner_identifier(
    partner_id: int,
    payload: IdentifierInput,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        row = partner_service.add_identifier(
            db, partner_id, kind=payload.kind, value=payload.value
        )
        sync = partner_master_service.rebuild_partner_master(
            db,
            actor=current_actor(request),
            run_payment_match=True,
        )
        db.commit()
        audit(
            db,
            current_actor(request),
            "business_partner.identifier_added",
            "business_partner",
            str(partner_id),
            {"kind": row.kind, "value": row.value, "sync": sync},
        )
        return partner_service.partner_detail(db, partner_id) or {}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.post("/{partner_id}/duplicates/{other_partner_id}/decide")
def decide_partner_duplicate(
    partner_id: int,
    other_partner_id: int,
    payload: DuplicateDecisionInput,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        detail = partner_service.decide_duplicate(
            db,
            partner_id,
            other_partner_id=other_partner_id,
            same=payload.same,
            note=payload.note,
            actor=current_actor(request),
        )
        db.commit()
        audit(
            db,
            current_actor(request),
            "business_partner.duplicate_decided",
            "business_partner",
            str(partner_id),
            {"otherPartnerId": other_partner_id, "same": payload.same, "note": payload.note},
        )
        return detail
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.post("/{partner_id}/review-links/{link_id}/claim")
def claim_partner_review_link(
    partner_id: int,
    link_id: int,
    payload: ReviewClaimInput,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        row = partner_service.claim_review_link(
            db, partner_id=partner_id, link_id=link_id, note=payload.note
        )
        rebuild = partner_master_service.rebuild_partner_master(
            db,
            actor=current_actor(request),
            run_payment_match=True,
        )
        db.commit()
        audit(
            db,
            current_actor(request),
            "business_partner.review_claimed",
            "business_partner_link",
            str(row.id),
            {
                "partnerId": partner_id,
                "sourceType": row.source_type,
                "sourceId": row.source_id,
                "materializedRefs": int(rebuild.get("materializedRefs", 0)),
                "bankInvoiceMatchesCreated": int(rebuild.get("bankInvoiceMatchesCreated", 0)),
            },
        )
        return partner_service.partner_detail(db, partner_id) or {}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
