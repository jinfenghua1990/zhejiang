"""税务做账：以官方发票为真值的月度底稿、财务大类汇总与分类规则。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_actor, require_roles
from app.core.audit import audit
from app.db import get_db
from app.models.org import User
from app.services import tax_accounting_service as service
from app.services import tax_category_rule_service as category_rule_service
from app.services import tax_finance_summary_service as finance_summary_service

router = APIRouter(prefix="/tax-accounting", tags=["tax-accounting"])


@router.get("/monthly-ledger")
def monthly_ledger(
    year: int = Query(..., ge=2000, le=9999),
    month: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return service.monthly_ledger(db, year, month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/finance-summary")
def finance_summary(
    year: int = Query(..., ge=2000, le=9999),
    month: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return finance_summary_service.build_finance_summary(db, year, month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/finance-summary.csv")
def finance_summary_csv(
    year: int = Query(..., ge=2000, le=9999),
    month: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
) -> Response:
    try:
        report = finance_summary_service.build_finance_summary(db, year, month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not report["readyForFinanceDelivery"]:
        raise HTTPException(status_code=409, detail={
            "message": "财务大类汇总仍有阻塞项，禁止生成发送文件",
            "blockers": report["blockers"],
        })
    content = finance_summary_service.to_finance_csv(report)
    headers = {
        "Content-Disposition": f'attachment; filename="finance_sales_summary_{year}{month:02d}.csv"'
    }
    return Response(content=content, media_type="text/csv; charset=utf-8", headers=headers)


class CategoryRuleCreate(BaseModel):
    pattern: str = Field(..., min_length=3, max_length=400, examples=["*软饮料*咖啡"])
    tax_code: str = Field("", max_length=32, description="开票用税收分类编码")
    match_keyword: str = Field("", max_length=256)
    match_mode: Literal["contains", "exact", "prefix"] = "contains"
    priority: int = Field(100, ge=0, le=9999)
    enabled: bool = True
    note: str = Field("", max_length=500)


class CategoryRuleUpdate(BaseModel):
    pattern: str | None = Field(None, min_length=3, max_length=400)
    tax_code: str | None = Field(None, max_length=32, description="开票用税收分类编码")
    match_keyword: str | None = Field(None, max_length=256)
    match_mode: Literal["contains", "exact", "prefix"] | None = None
    priority: int | None = Field(None, ge=0, le=9999)
    enabled: bool | None = None
    note: str | None = Field(None, max_length=500)


@router.get("/category-rules")
def get_category_rules(
    include_disabled: bool = Query(True),
    db: Session = Depends(get_db),
) -> dict:
    """读取用户自己维护的做账分类规则。"""
    return {
        "items": category_rule_service.list_rules(db, include_disabled=include_disabled),
        "example": "*软饮料*咖啡",
        "matchingOrder": ["官方分类字段", "官方*大类*项目格式", "用户维护规则"],
    }


@router.post("/category-rules")
def add_category_rule(
    body: CategoryRuleCreate,
    request: Request,
    _editor: User = Depends(require_roles("admin", "operator")),
    db: Session = Depends(get_db),
) -> dict:
    actor = current_actor(request)
    try:
        row = category_rule_service.create_rule(
            db,
            pattern=body.pattern,
            tax_code=body.tax_code,
            match_keyword=body.match_keyword,
            match_mode=body.match_mode,
            priority=body.priority,
            enabled=body.enabled,
            note=body.note,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(
        db,
        actor,
        "tax_accounting.category_rule.create",
        "tax_accounting_category_rules",
        row.id,
        {"pattern": category_rule_service.build_pattern(row.category_name, row.item_name)},
    )
    return category_rule_service.serialize_rule(row)


@router.patch("/category-rules/{rule_id}")
def edit_category_rule(
    rule_id: int,
    body: CategoryRuleUpdate,
    request: Request,
    _editor: User = Depends(require_roles("admin", "operator")),
    db: Session = Depends(get_db),
) -> dict:
    actor = current_actor(request)
    try:
        row = category_rule_service.update_rule(
            db,
            rule_id,
            pattern=body.pattern,
            tax_code=body.tax_code,
            match_keyword=body.match_keyword,
            match_mode=body.match_mode,
            priority=body.priority,
            enabled=body.enabled,
            note=body.note,
            actor=actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(
        db,
        actor,
        "tax_accounting.category_rule.update",
        "tax_accounting_category_rules",
        row.id,
        {
            "pattern": category_rule_service.build_pattern(row.category_name, row.item_name),
            "taxCode": row.tax_code or "",
            "enabled": row.enabled,
            "priority": row.priority,
        },
    )
    return category_rule_service.serialize_rule(row)
