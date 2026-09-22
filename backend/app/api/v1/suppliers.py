from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.core.audit import audit
from app.db import get_db
from app.models.business_partner import BusinessPartner, BusinessPartnerIdentifier
from app.models.purchase import Supplier
from app.services import business_partner_service
from app.services.procurement_chain_service import supplier_summaries
from app.services.supplier_sync_service import normalize_supplier_name, sync_suppliers_from_business_data

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


class SupplierInput(BaseModel):
    """Supplier 是采购画像；主体名称/税号/联系人最终维护在 BusinessPartner。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    name: str = Field(min_length=1, max_length=256)
    platform: str = Field(default="", max_length=32)
    external_shop_id: str = Field(default="", max_length=128)
    contact: str = Field(default="", max_length=256)
    tax_no: str = Field(default="", max_length=64)
    phone: str = Field(default="", max_length=64)
    address: str = Field(default="", max_length=512)
    notes: str = Field(default="", max_length=512)
    is_temp: bool = False


def _former_names_by_partner(db: Session, partner_ids: list[int]) -> dict[int, list[str]]:
    if not partner_ids:
        return {}
    rows = (
        db.query(BusinessPartnerIdentifier.partner_id, BusinessPartnerIdentifier.value)
        .filter(
            BusinessPartnerIdentifier.partner_id.in_(partner_ids),
            BusinessPartnerIdentifier.kind == "former_name",
        )
        .order_by(BusinessPartnerIdentifier.id)
        .all()
    )
    result: dict[int, list[str]] = {}
    for partner_id, value in rows:
        result.setdefault(int(partner_id), []).append(value)
    return result


def _serialize(
    profile: Supplier,
    *,
    partner: BusinessPartner | None = None,
    order_count: int | None = None,
    former_names: list[str] | None = None,
) -> dict[str, Any]:
    """供应商列表以 canonical partner 为主档，Supplier 仅提供采购画像字段。"""
    canonical = partner if partner is not None and partner.status == "active" else None
    data: dict[str, Any] = {
        "id": profile.id,
        "partnerId": canonical.id if canonical is not None else profile.partner_id,
        "name": canonical.name if canonical is not None else profile.name,
        "platform": profile.platform or "",
        "externalShopId": profile.external_shop_id or "",
        "contact": canonical.contact if canonical is not None else (profile.contact or ""),
        "taxNo": canonical.tax_no if canonical is not None else (profile.tax_no or ""),
        "phone": canonical.phone if canonical is not None else (profile.phone or ""),
        "address": canonical.address if canonical is not None else (profile.address or ""),
        # Supplier.notes 是采购画像备注；主体通用备注只在 BusinessPartner 主档维护。
        "notes": profile.notes or "",
        "isTemp": bool(profile.is_temp),
        "purchaseType": "regular" if (order_count or 0) >= 2 else "temporary",
        "createdAt": (
            canonical.created_at.isoformat()
            if canonical is not None and canonical.created_at
            else profile.created_at.isoformat() if profile.created_at else None
        ),
        "formerNames": list(former_names or []),
    }
    if order_count is not None:
        data["orderCount"] = order_count
    return data


def _profile_for_partner(
    partner: BusinessPartner,
    profiles: list[Supplier],
) -> Supplier | None:
    if not profiles:
        return None
    if partner.legacy_supplier_id is not None:
        matched = next((row for row in profiles if row.id == partner.legacy_supplier_id), None)
        if matched is not None:
            return matched
    return sorted(profiles, key=lambda row: row.id)[0]


def _sync_master(db: Session) -> dict[str, Any]:
    supplier_sync = sync_suppliers_from_business_data(db)
    partner_sync = business_partner_service.sync_business_partners(db)
    db.commit()
    return {"supplierSync": supplier_sync, "partnerSync": partner_sync}


@router.get("")
def list_suppliers(
    keyword: str = "",
    status: str = Query("all", pattern="^(all|regular|temporary|normal|temp)$"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """供应商档案 = 具有真实采购事实的 canonical BusinessPartner 供应商视图。"""
    sync = _sync_master(db)
    if (
        sync["supplierSync"].get("created")
        or sync["supplierSync"].get("updated")
        or sync["partnerSync"].get("materializedRefs")
    ):
        audit(db, "system", "supplier.master_sync", "suppliers", "", sync)

    summary = supplier_summaries(db, limit=100_000, offset=0)
    summary_items = [
        item for item in summary["items"] if int(item.get("orderCount") or 0) > 0
    ]
    if not summary_items:
        return []

    partner_ids = {
        int(item["partnerId"])
        for item in summary_items
        if item.get("partnerId") is not None
    }
    partners = {
        int(row.id): row
        for row in (
            db.query(BusinessPartner)
            .filter(
                BusinessPartner.id.in_(partner_ids),
                BusinessPartner.status == "active",
            )
            .all()
            if partner_ids
            else []
        )
    }
    profiles_by_partner: dict[int, list[Supplier]] = {}
    if partner_ids:
        for profile in (
            db.query(Supplier)
            .filter(Supplier.partner_id.in_(partner_ids))
            .order_by(Supplier.id)
            .all()
        ):
            profiles_by_partner.setdefault(int(profile.partner_id), []).append(profile)

    # Compatibility fallback: facts still awaiting canonical materialization remain visible
    # by the old exact normalized supplier name, but are not merged with another party.
    unresolved_names = {
        normalize_supplier_name(item.get("supplierName"))
        for item in summary_items
        if item.get("partnerId") is None
    }
    fallback_profiles = {
        normalize_supplier_name(row.name): row
        for row in db.query(Supplier).all()
        if normalize_supplier_name(row.name) in unresolved_names
    }

    former_names = _former_names_by_partner(db, list(partner_ids))
    needle = keyword.strip().casefold()
    result: list[dict[str, Any]] = []

    for item in summary_items:
        count = int(item.get("orderCount") or 0)
        if status in {"regular", "normal"} and count < 2:
            continue
        if status in {"temporary", "temp"} and count != 1:
            continue

        partner_id = item.get("partnerId")
        if partner_id is not None:
            partner = partners.get(int(partner_id))
            if partner is None:
                continue
            profile = _profile_for_partner(
                partner,
                profiles_by_partner.get(int(partner_id), []),
            )
            if profile is None:
                # A real purchase should normally have a Supplier profile after _sync_master.
                # Do not fabricate an ID here; leave the inconsistency visible to coverage/audit.
                continue
            aliases = former_names.get(int(partner_id), [])
            haystack = " ".join(
                [
                    partner.name or "",
                    partner.tax_no or "",
                    partner.contact or "",
                    partner.phone or "",
                    *aliases,
                    *(row.name or "" for row in profiles_by_partner.get(int(partner_id), [])),
                ]
            ).casefold()
            if needle and needle not in haystack:
                continue
            result.append(
                _serialize(
                    profile,
                    partner=partner,
                    order_count=count,
                    former_names=aliases,
                )
            )
            continue

        fallback_name = normalize_supplier_name(item.get("supplierName"))
        profile = fallback_profiles.get(fallback_name)
        if profile is None:
            continue
        haystack = " ".join(
            [profile.name or "", profile.tax_no or "", profile.contact or "", profile.phone or ""]
        ).casefold()
        if needle and needle not in haystack:
            continue
        result.append(_serialize(profile, order_count=count))

    result.sort(key=lambda row: (row["name"], row["partnerId"] or 0, row["id"]))
    return result


@router.post("")
def create_supplier(
    payload: SupplierInput, request: Request, db: Session = Depends(get_db)
) -> dict[str, Any]:
    row = Supplier(
        name=payload.name.strip(),
        platform=payload.platform.strip(),
        external_shop_id=payload.external_shop_id.strip(),
        contact=payload.contact.strip(),
        tax_no=business_partner_service.normalize_tax_no(payload.tax_no),
        phone=payload.phone.strip(),
        address=payload.address.strip(),
        notes=payload.notes.strip(),
        is_temp=payload.is_temp,
    )
    db.add(row)
    db.flush()
    business_partner_service.sync_business_partners(db)
    db.commit()
    db.refresh(row)
    partner = db.get(BusinessPartner, row.partner_id) if row.partner_id else None
    audit(
        db,
        current_actor(request),
        "supplier.profile_created",
        "supplier",
        str(row.id),
        {"name": row.name, "partnerId": row.partner_id},
    )
    return _serialize(row, partner=partner, order_count=0)


@router.put("/{supplier_id}")
def update_supplier(
    supplier_id: int,
    payload: SupplierInput,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = db.get(Supplier, supplier_id)
    if not row:
        raise HTTPException(404, "供应商不存在")

    partner = db.get(BusinessPartner, row.partner_id) if row.partner_id else None

    # V2: Supplier 只维护采购渠道画像，不再反向覆盖统一往来主体。
    # 名称/税号/联系人/电话/地址/银行账户只能在 BusinessPartner 主档维护。
    row.platform = payload.platform.strip()
    row.external_shop_id = payload.external_shop_id.strip()
    row.notes = payload.notes.strip()
    row.is_temp = payload.is_temp

    if partner is not None and partner.status == "active":
        # 兼容旧报表：legacy Supplier 字段只镜像 canonical 主档，不再成为写入源。
        row.contact = partner.contact or ""
        row.tax_no = partner.tax_no or ""
        row.phone = partner.phone or ""
        row.address = partner.address or ""
    else:
        # 仅对尚未完成 canonical 迁移的旧资料保留兼容编辑能力；
        # 本次同步完成后会自动生成/绑定 BusinessPartner。
        row.name = payload.name.strip()
        row.contact = payload.contact.strip()
        row.tax_no = business_partner_service.normalize_tax_no(payload.tax_no)
        row.phone = payload.phone.strip()
        row.address = payload.address.strip()

    business_partner_service.sync_business_partners(db)
    db.commit()
    db.refresh(row)
    partner = db.get(BusinessPartner, row.partner_id) if row.partner_id else None
    audit(
        db,
        current_actor(request),
        "supplier.profile_updated",
        "supplier",
        str(row.id),
        {
            "name": row.name,
            "partnerId": row.partner_id,
            "platform": row.platform,
            "externalShopId": row.external_shop_id,
            "scope": "procurement_profile_only",
        },
    )
    return _serialize(row, partner=partner)


@router.delete("/{supplier_id}")
def delete_supplier(
    supplier_id: int, request: Request, db: Session = Depends(get_db)
) -> dict[str, Any]:
    row = db.get(Supplier, supplier_id)
    if not row:
        raise HTTPException(404, "供应商不存在")

    if row.partner_id is not None:
        summary = supplier_summaries(db, limit=100_000, offset=0)
        item = next(
            (
                item for item in summary["items"]
                if item.get("partnerId") == row.partner_id
                and int(item.get("orderCount") or 0) > 0
            ),
            None,
        )
        if item is not None:
            raise HTTPException(
                409,
                "该主体已有真实采购事实，不能删除供应商主档；请在往来单位主档处理状态。",
            )

    audit(
        db,
        current_actor(request),
        "supplier.profile_deleted",
        "supplier",
        str(row.id),
        {"name": row.name, "partnerId": row.partner_id},
    )
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/resolve")
def resolve_suppliers(payload: dict[str, list[str]], db: Session = Depends(get_db)) -> dict[str, Any]:
    """按 canonical 税号识别供应商主体；同一主体的多平台画像不会制造重复身份。"""
    tax_nos = [
        business_partner_service.normalize_tax_no(value)
        for value in (payload.get("taxNos") or [])
        if business_partner_service.normalize_tax_no(value)
    ]
    if not tax_nos:
        return {"matched": {}, "unmatched": []}

    partners = (
        db.query(BusinessPartner)
        .filter(
            BusinessPartner.tax_no.in_(list(set(tax_nos))),
            BusinessPartner.status == "active",
        )
        .all()
    )
    matched: dict[str, dict[str, Any]] = {}
    for partner in partners:
        profile = _profile_for_partner(
            partner,
            db.query(Supplier)
            .filter(Supplier.partner_id == partner.id)
            .order_by(Supplier.id)
            .all(),
        )
        if profile is None:
            continue
        matched[partner.tax_no] = {
            "id": profile.id,
            "partnerId": partner.id,
            "name": partner.name,
            "isTemp": bool(profile.is_temp),
        }

    unmatched = [tax_no for tax_no in tax_nos if tax_no not in matched]
    return {"matched": matched, "unmatched": unmatched}
