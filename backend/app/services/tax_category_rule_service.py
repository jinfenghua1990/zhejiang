"""税务做账财务大类规则管理。

用户可维护类似 `*软饮料*咖啡` 的规则。官方发票已明确给出税收分类时仍优先采用官方字段；
只有官方分类缺失时，才允许使用用户显式维护并启用的规则进行归类。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from sqlalchemy.orm import Session

from app.models.tax import TaxAccountingCategoryRule

MATCH_MODES = {"contains", "exact", "prefix"}
TAX_CODE_LENGTHS = {10, 19, 21}
_PATTERN_RE = re.compile(r"^\s*[＊*]([^＊*]+)[＊*](.+?)\s*$")


def _norm(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def parse_pattern(pattern: str) -> tuple[str, str]:
    """把 `*软饮料*咖啡` 拆成 (`软饮料`, `咖啡`)。"""
    match = _PATTERN_RE.match(pattern or "")
    if not match:
        raise ValueError("格式必须类似：*软饮料*咖啡")
    category = match.group(1).strip()
    item = match.group(2).strip()
    if not category or not item:
        raise ValueError("财务大类和项目名称都不能为空")
    return category, item


def build_pattern(category_name: str, item_name: str) -> str:
    return f"*{category_name.strip()}*{item_name.strip()}"


def normalize_tax_code(value: object) -> str:
    """规范并校验开票用税收分类编码；空值表示规则尚未配置代码。"""
    code = str(value or "").strip()
    if code and (not code.isdigit() or len(code) not in TAX_CODE_LENGTHS):
        raise ValueError("税务代码应为 19 位税收分类编码（兼容旧 10 位或 21 位编码）")
    return code


def serialize_rule(row: TaxAccountingCategoryRule) -> dict[str, Any]:
    return {
        "id": row.id,
        "pattern": build_pattern(row.category_name, row.item_name),
        "categoryName": row.category_name,
        "itemName": row.item_name,
        "taxCode": row.tax_code or "",
        "matchKeyword": row.match_keyword or row.item_name,
        "matchMode": row.match_mode,
        "priority": row.priority,
        "enabled": row.enabled,
        "note": row.note or "",
        "createdBy": row.created_by,
        "updatedBy": row.updated_by,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_rules(db: Session, *, include_disabled: bool = True) -> list[dict[str, Any]]:
    query = db.query(TaxAccountingCategoryRule)
    if not include_disabled:
        query = query.filter(TaxAccountingCategoryRule.enabled.is_(True))
    rows = query.order_by(TaxAccountingCategoryRule.priority, TaxAccountingCategoryRule.id).all()
    return [serialize_rule(row) for row in rows]


def enabled_rule_rows(db: Session) -> list[TaxAccountingCategoryRule]:
    return (
        db.query(TaxAccountingCategoryRule)
        .filter(TaxAccountingCategoryRule.enabled.is_(True))
        .order_by(TaxAccountingCategoryRule.priority, TaxAccountingCategoryRule.id)
        .all()
    )


def unique_enabled_rule_for_tax_code(
    db: Session, tax_code: str
) -> TaxAccountingCategoryRule | None:
    """返回一个税务代码唯一对应的启用规则；多条规则时不擅自选择。"""
    code = normalize_tax_code(tax_code)
    if not code:
        return None
    rows = (
        db.query(TaxAccountingCategoryRule)
        .filter(
            TaxAccountingCategoryRule.enabled.is_(True),
            TaxAccountingCategoryRule.tax_code == code,
        )
        .order_by(TaxAccountingCategoryRule.priority, TaxAccountingCategoryRule.id)
        .all()
    )
    return rows[0] if len(rows) == 1 else None


def link_unambiguous_catalog_rows(
    db: Session, row: TaxAccountingCategoryRule
) -> dict[str, int]:
    """把已有直填代码的主档关联到唯一规则，不覆盖已有人工关联。"""
    if not row.enabled or not row.tax_code:
        return {"products": 0, "consumables": 0}
    matching = (
        db.query(TaxAccountingCategoryRule.id)
        .filter(
            TaxAccountingCategoryRule.enabled.is_(True),
            TaxAccountingCategoryRule.tax_code == row.tax_code,
        )
        .all()
    )
    if len(matching) != 1:
        return {"products": 0, "consumables": 0}
    from app.models.catalog import ProductSku
    from app.models.consumable import Consumable

    product_count = (
        db.query(ProductSku)
        .filter(
            ProductSku.tax_code == row.tax_code,
            ProductSku.tax_category_rule_id.is_(None),
        )
        .update({ProductSku.tax_category_rule_id: row.id}, synchronize_session=False)
    )
    consumable_count = (
        db.query(Consumable)
        .filter(
            Consumable.tax_code == row.tax_code,
            Consumable.tax_category_rule_id.is_(None),
        )
        .update({Consumable.tax_category_rule_id: row.id}, synchronize_session=False)
    )
    return {"products": product_count, "consumables": consumable_count}


def _validate_mode(mode: str) -> str:
    normalized = (mode or "contains").strip().lower()
    if normalized not in MATCH_MODES:
        raise ValueError("matchMode 仅支持 contains / exact / prefix")
    return normalized


def create_rule(
    db: Session,
    *,
    pattern: str,
    tax_code: str = "",
    match_keyword: str = "",
    match_mode: str = "contains",
    priority: int = 100,
    enabled: bool = True,
    note: str = "",
    actor: str = "system",
) -> TaxAccountingCategoryRule:
    category, item = parse_pattern(pattern)
    exists = (
        db.query(TaxAccountingCategoryRule)
        .filter_by(category_name=category, item_name=item)
        .first()
    )
    if exists:
        raise ValueError("这条分类规则已经存在")
    row = TaxAccountingCategoryRule(
        category_name=category,
        item_name=item,
        tax_code=normalize_tax_code(tax_code),
        match_keyword=(match_keyword or item).strip(),
        match_mode=_validate_mode(match_mode),
        priority=max(0, min(int(priority), 9999)),
        enabled=bool(enabled),
        note=(note or "").strip(),
        created_by=actor or "system",
        updated_by=actor or "system",
    )
    db.add(row)
    db.flush()
    link_unambiguous_catalog_rows(db, row)
    db.commit()
    db.refresh(row)
    return row


def update_rule(
    db: Session,
    rule_id: int,
    *,
    pattern: str | None = None,
    tax_code: str | None = None,
    match_keyword: str | None = None,
    match_mode: str | None = None,
    priority: int | None = None,
    enabled: bool | None = None,
    note: str | None = None,
    actor: str = "system",
) -> TaxAccountingCategoryRule:
    row = db.get(TaxAccountingCategoryRule, rule_id)
    if row is None:
        raise LookupError("分类规则不存在")
    if pattern is not None:
        category, item = parse_pattern(pattern)
        duplicate = (
            db.query(TaxAccountingCategoryRule)
            .filter(
                TaxAccountingCategoryRule.id != rule_id,
                TaxAccountingCategoryRule.category_name == category,
                TaxAccountingCategoryRule.item_name == item,
            )
            .first()
        )
        if duplicate:
            raise ValueError("修改后的分类规则已经存在")
        row.category_name = category
        row.item_name = item
        if match_keyword is None and not row.match_keyword:
            row.match_keyword = item
    if tax_code is not None:
        row.tax_code = normalize_tax_code(tax_code)
    if match_keyword is not None:
        row.match_keyword = (match_keyword or row.item_name).strip()
    if match_mode is not None:
        row.match_mode = _validate_mode(match_mode)
    if priority is not None:
        row.priority = max(0, min(int(priority), 9999))
    if enabled is not None:
        row.enabled = bool(enabled)
    if note is not None:
        row.note = note.strip()
    _sync_linked_catalog_tax_code(db, row)
    link_unambiguous_catalog_rows(db, row)
    row.updated_by = actor or "system"
    db.commit()
    db.refresh(row)
    return row


def _sync_linked_catalog_tax_code(db: Session, row: TaxAccountingCategoryRule) -> None:
    """让已经引用该规则的正品/耗材档案跟随规则的税务代码。"""
    from app.models.catalog import ProductSku
    from app.models.consumable import Consumable

    db.query(ProductSku).filter(ProductSku.tax_category_rule_id == row.id).update(
        {ProductSku.tax_code: row.tax_code or ""}, synchronize_session=False
    )
    db.query(Consumable).filter(Consumable.tax_category_rule_id == row.id).update(
        {Consumable.tax_code: row.tax_code or ""}, synchronize_session=False
    )


def match_rule(goods_name: str, rules: list[TaxAccountingCategoryRule]) -> TaxAccountingCategoryRule | None:
    """按用户显式维护的启用规则匹配。priority 越小优先级越高。"""
    value = _norm(goods_name)
    if not value:
        return None
    for row in rules:
        keyword = _norm(row.match_keyword or row.item_name)
        if not keyword:
            continue
        if row.match_mode == "exact" and value == keyword:
            return row
        if row.match_mode == "prefix" and value.startswith(keyword):
            return row
        if row.match_mode == "contains" and keyword in value:
            return row
    return None
