"""基础档案 Excel 回导。

只更新可维护的主档字段，不改库存流水、入库事实或采购状态机。
每个文件先完成整表校验，再一次性写入，避免半张表成功、半张表失败。
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.models.catalog import Product, ProductSku, Warehouse
from app.models.consumable import Consumable, ConsumableSkuMapping
from app.models.purchase import ExternalPurchaseOrder, Supplier
from app.models.tax import TaxAccountingCategoryRule
from app.services import purchase_service, tax_category_rule_service, warehouse_service


DATASETS = {"catalog", "bundles", "tax_rules", "warehouses", "suppliers", "external_orders"}
MAX_ROWS = 20_000


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    return re.sub(r"[\s_\-()（）\[\]【】/\\:：]+", "", text)


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _read_table(content: bytes, filename: str, aliases: tuple[str, ...]) -> tuple[list[str], list[dict[str, Any]], str]:
    if not content:
        raise ValueError("空文件")
    if Path(filename or "").suffix.lower() not in {".xlsx", ".xlsm"}:
        raise ValueError("基础档案仅支持 .xlsx 或 .xlsm")
    try:
        from openpyxl import load_workbook

        book = load_workbook(BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError(f"Excel 文件无法读取：{exc}") from exc

    alias_keys = {_norm(alias) for alias in aliases}
    selected: tuple[int, str, list[list[Any]]] | None = None
    try:
        for sheet in book.worksheets:
            table: list[list[Any]] = []
            for index, values in enumerate(sheet.iter_rows(values_only=True)):
                if index >= MAX_ROWS + 20:
                    raise ValueError(f"单个工作表超过 {MAX_ROWS} 行")
                table.append(list(values))
            for index, values in enumerate(table[:20]):
                score = sum(1 for value in values if _norm(value) in alias_keys)
                if score and (selected is None or score > selected[0]):
                    selected = (score, sheet.title, table)
        if selected is None:
            raise ValueError("未找到对应的基础档案表头")
        _, sheet_name, table = selected
        header_index = next(
            index for index, values in enumerate(table[:20])
            if sum(1 for value in values if _norm(value) in alias_keys)
        )
        headers: list[str] = []
        used: dict[str, int] = {}
        for index, value in enumerate(table[header_index], start=1):
            base = _text(value) or f"列{index}"
            used[base] = used.get(base, 0) + 1
            headers.append(base if used[base] == 1 else f"{base}#{used[base]}")

        rows: list[dict[str, Any]] = []
        for source_index, values in enumerate(table[header_index + 1 :], start=header_index + 2):
            payload = {
                headers[index]: values[index]
                for index in range(min(len(headers), len(values)))
                if _text(values[index])
            }
            if payload:
                payload["__row__"] = source_index
                rows.append(payload)
        if not rows:
            raise ValueError("表格没有可导入的数据行")
        return headers, rows, sheet_name
    finally:
        book.close()


def _has(headers: list[str], aliases: tuple[str, ...]) -> bool:
    keys = {_norm(header) for header in headers}
    return any(_norm(alias) in keys for alias in aliases)


def _value(row: dict[str, Any], aliases: tuple[str, ...]) -> str:
    normalized = {_norm(key): value for key, value in row.items()}
    for alias in aliases:
        key = _norm(alias)
        if key in normalized:
            return _text(normalized[key])
    return ""


def _row_no(row: dict[str, Any]) -> int:
    return int(row.get("__row__") or 0)


def _decimal_row(row: dict[str, Any], aliases: tuple[str, ...], label: str) -> Decimal | None:
    raw = _value(row, aliases)
    if not raw:
        return None
    try:
        result = Decimal(raw.replace(",", "").replace("¥", "").replace("￥", ""))
    except InvalidOperation as exc:
        raise ValueError(f"第 {_row_no(row)} 行{label}不是有效数字") from exc
    if not result.is_finite():
        raise ValueError(f"第 {_row_no(row)} 行{label}不能是无穷或非数字")
    return result


def _int_row(row: dict[str, Any], aliases: tuple[str, ...], label: str) -> int | None:
    raw = _value(row, aliases)
    if not raw:
        return None
    try:
        value = Decimal(raw.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"第 {_row_no(row)} 行{label}不是有效整数") from exc
    if value != value.to_integral_value():
        raise ValueError(f"第 {_row_no(row)} 行{label}必须是整数")
    return int(value)


def _datetime_row(row: dict[str, Any], aliases: tuple[str, ...], label: str) -> datetime | None:
    raw = _value(row, aliases)
    if not raw:
        return None
    text = raw.replace("年", "-").replace("月", "-").replace("日", "")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    for parser in (
        lambda value: datetime.fromisoformat(value),
        lambda value: datetime.strptime(value, "%Y/%m/%d %H:%M:%S"),
        lambda value: datetime.strptime(value, "%Y/%m/%d"),
        lambda value: datetime.strptime(value, "%Y-%m-%d"),
    ):
        try:
            parsed = parser(text)
            return parsed if isinstance(parsed, datetime) else datetime.combine(parsed, datetime.min.time())
        except (TypeError, ValueError):
            continue
    raise ValueError(f"第 {_row_no(row)} 行{label}格式无法识别")


def _bool_row(row: dict[str, Any], aliases: tuple[str, ...], label: str, default: bool = False) -> bool:
    raw = _value(row, aliases).lower()
    if not raw:
        return default
    if raw in {"是", "true", "1", "y", "yes", "启用"}:
        return True
    if raw in {"否", "false", "0", "n", "no", "停用"}:
        return False
    raise ValueError(f"第 {_row_no(row)} 行{label}只能填写是/否")


def _status(value: str, row: dict[str, Any]) -> str:
    raw = value.strip().lower()
    if raw in {"", "active", "启用", "正常"}:
        return "active"
    if raw in {"inactive", "停用", "禁用"}:
        return "inactive"
    raise ValueError(f"第 {_row_no(row)} 行状态只能填写启用或停用")


def _kind(value: str, row: dict[str, Any]) -> str:
    raw = value.strip().lower()
    if raw in {"正品", "货品", "goods", "商品"}:
        return "goods"
    if raw in {"耗材", "consumable", "包材"}:
        return "consumable"
    raise ValueError(f"第 {_row_no(row)} 行类型只能填写正品或耗材")


def _product_type(value: str, row: dict[str, Any]) -> str:
    raw = value.strip().lower()
    values = {
        "": "single", "单品": "single", "single": "single",
        "套装": "bundle", "bundle": "bundle",
        "虚拟组合套装": "virtual_bundle", "virtual_bundle": "virtual_bundle",
    }
    if raw not in values:
        raise ValueError(f"第 {_row_no(row)} 行货品类型只能填写单品、套装或虚拟组合套装")
    return values[raw]


def _tax_values(db: Session, row: dict[str, Any], headers: list[str]) -> tuple[str, int | None, bool]:
    has_code = _has(headers, ("税务代码", "税收分类编码"))
    has_rule = _has(headers, ("财务分类ID", "税务分类ID"))
    if not has_code and not has_rule:
        return "", None, False
    rule_id = _int_row(row, ("财务分类ID", "税务分类ID"), "财务分类ID") if has_rule else None
    tax_code = _value(row, ("税务代码", "税收分类编码")) if has_code else ""
    if rule_id is not None:
        rule = db.get(TaxAccountingCategoryRule, rule_id)
        if rule is None:
            raise ValueError(f"第 {_row_no(row)} 行财务分类ID不存在")
        return rule.tax_code or "", rule.id, True
    normalized = tax_category_rule_service.normalize_tax_code(tax_code)
    rule = tax_category_rule_service.unique_enabled_rule_for_tax_code(db, normalized)
    return normalized, rule.id if rule is not None else None, True


def _link_refs(value: str) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            result = []
            for item in parsed:
                if isinstance(item, dict):
                    text = _text(item.get("skuCode") or item.get("skuId"))
                else:
                    text = _text(item)
                if text:
                    result.append(text)
            return result
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return [item.strip() for item in re.split(r"[,，;；、\n]+", value) if item.strip()]


def _find_sku(db: Session, ref: str) -> ProductSku | None:
    rows = db.query(ProductSku).filter(
        (ProductSku.sku_code == ref) | (ProductSku.jackyun_sku_id == ref)
    ).all()
    if len(rows) > 1:
        raise ValueError(f"正品 SKU {ref} 对应多个档案，无法自动选择")
    if rows:
        return rows[0]
    if ref.isdigit():
        return db.get(ProductSku, int(ref))
    return None


def import_catalog(
    db: Session,
    content: bytes,
    filename: str,
    actor: str,
    *,
    scope: str = "catalog",
) -> dict[str, Any]:
    if scope not in {"catalog", "bundles"}:
        raise ValueError("不支持的货品档案范围")
    dataset = "bundles" if scope == "bundles" else "catalog"
    headers, rows, sheet = _read_table(
        content,
        filename,
        ("类型", "编码", "货品编号", "SKU编码", "名称", "货品名称", "耗材名称"),
    )
    if not _has(headers, ("类型",)) or not _has(headers, ("编码", "货品编号", "SKU编码", "耗材编码")):
        raise ValueError("货品档案必须包含“类型”和“编码”列")
    seen_codes: set[tuple[str, str]] = set()
    specs: list[dict[str, Any]] = []
    skipped = 0
    for row in rows:
        kind = _kind(_value(row, ("类型",)), row)
        code = _value(row, ("编码", "货品编号", "SKU编码", "耗材编码"))
        name = _value(row, ("名称", "货品名称", "商品名称", "耗材名称", "产品名"))
        if not code and not name:
            skipped += 1
            continue
        if not code or not name:
            raise ValueError(f"第 {_row_no(row)} 行编码和名称不能为空")
        code_key = (kind, code.casefold())
        if code_key in seen_codes:
            raise ValueError(f"第 {_row_no(row)} 行与本文件前面的{kind}编码重复：{code}")
        seen_codes.add(code_key)
        item: dict[str, Any] = {
            "row": row,
            "kind": kind,
            "id": _int_row(row, ("档案ID", "货品ID", "SKU ID"), "档案ID"),
            "code": code,
            "name": name,
            "headers": headers,
        }
        item["status"] = _status(_value(row, ("状态", "档案状态")), row) if _has(headers, ("状态", "档案状态")) else "active"
        item["tax"] = _tax_values(db, row, headers)
        if kind == "goods":
            item.update({
                "external_id": _value(row, ("吉客云SKU ID", "吉客云 SKU ID", "外部SKU ID")),
                "product_type": _product_type(_value(row, ("货品类型", "SKU类型", "产品类型")), row) if _has(headers, ("货品类型", "SKU类型", "产品类型")) else "single",
                "barcode": _value(row, ("条码", "货品条码")),
                "unit": _value(row, ("单位",)),
                "sale_price": _decimal_row(row, ("售价", "默认售价"), "售价"),
                "default_cost": _decimal_row(row, ("默认成本",), "默认成本"),
                "cost_mode": _value(row, ("成本方式",)).lower() if _has(headers, ("成本方式",)) else "fixed",
                "tolerance": _decimal_row(row, ("成本容差",), "成本容差"),
                "category": _value(row, ("品类", "商品品类")),
            })
            if item["cost_mode"] not in {"fixed", "dynamic"}:
                raise ValueError(f"第 {_row_no(row)} 行成本方式只能填写固定成本/fixed或动态成本/dynamic")
            for label, value in (("售价", item["sale_price"]), ("默认成本", item["default_cost"])):
                if value is not None and value < 0:
                    raise ValueError(f"第 {_row_no(row)} 行{label}不能为负数")
            if item["tolerance"] is not None and not Decimal("0") <= item["tolerance"] <= Decimal("1"):
                raise ValueError(f"第 {_row_no(row)} 行成本容差必须在 0 到 1 之间")
            if not item["external_id"]:
                item["external_id"] = code
        else:
            item.update({
                "barcode": _value(row, ("条码", "耗材条码")),
                "unit": _value(row, ("单位",)) or "个",
                "category": _value(row, ("品类", "分类")),
                "purchase_cost": _decimal_row(row, ("采购单价", "采购单位成本"), "采购单价"),
                "min_stock": _decimal_row(row, ("安全库存", "最低库存", "最小库存"), "安全库存"),
                "links": _link_refs(_value(row, ("关联正品SKU", "关联正品编码"))) if _has(headers, ("关联正品SKU", "关联正品编码")) else None,
            })
            for label, value in (("采购单价", item["purchase_cost"]), ("安全库存", item["min_stock"])):
                if value is not None and value < 0:
                    raise ValueError(f"第 {_row_no(row)} 行{label}不能为负数")
        if scope == "catalog" and kind == "goods" and item["product_type"] != "single":
            raise ValueError(f"第 {_row_no(row)} 行是套装，请使用“套装”导入模板")
        if scope == "bundles" and (kind != "goods" or item.get("product_type") not in {"bundle", "virtual_bundle"}):
            raise ValueError(f"第 {_row_no(row)} 行不是套装，套装模板只接受套装或虚拟组合套装")
        specs.append(item)

    created = updated = mappings_created = mappings_removed = 0
    affected_skus: set[int] = set()
    # 先处理正品，保证同一个文件里的耗材关联可以引用本批新增 SKU。
    for item in [spec for spec in specs if spec["kind"] == "goods"]:
        sku = db.get(ProductSku, item["id"]) if item["id"] is not None else None
        if item["id"] is not None and sku is None:
            raise ValueError(f"第 {_row_no(item['row'])} 行货品档案ID不存在：{item['id']}")
        if sku is None:
            matches = db.query(ProductSku).filter(ProductSku.sku_code == item["code"]).all()
            if len(matches) > 1:
                raise ValueError(f"货品编码 {item['code']} 对应多个 SKU")
            sku = matches[0] if matches else None
        if sku is None:
            external_matches = db.query(ProductSku).filter(ProductSku.jackyun_sku_id == item["external_id"]).all()
            if len(external_matches) > 1:
                raise ValueError(f"吉客云 SKU ID {item['external_id']} 对应多个档案")
            sku = external_matches[0] if external_matches else None
        if sku is None:
            sku = ProductSku(jackyun_sku_id=item["external_id"], sku_code=item["code"])
            db.add(sku)
            created += 1
        else:
            updated += 1
        code_conflict = db.query(ProductSku).filter(func.lower(ProductSku.sku_code) == item["code"].lower())
        if sku.id is not None:
            code_conflict = code_conflict.filter(ProductSku.id != sku.id)
        if code_conflict.first():
            raise ValueError(f"货品编码已存在：{item['code']}")
        conflict = db.query(ProductSku).filter(ProductSku.jackyun_sku_id == item["external_id"])
        if sku.id is not None:
            conflict = conflict.filter(ProductSku.id != sku.id)
        if conflict.first():
            raise ValueError(f"吉客云 SKU ID 已存在：{item['external_id']}")
        sku.jackyun_sku_id = item["external_id"]
        sku.sku_code = item["code"]
        sku.product_type = item["product_type"]
        sku.sku_name = item["name"]
        sku.barcode = item["barcode"]
        sku.unit = item["unit"]
        sku.sale_price = item["sale_price"]
        sku.default_cost = item["default_cost"]
        sku.cost_mode = item["cost_mode"]
        if item["tolerance"] is not None:
            sku.cost_tolerance_pct = item["tolerance"]
        sku.status = item["status"]
        tax_code, tax_rule_id, has_tax = item["tax"]
        if has_tax:
            sku.tax_code = tax_code
            sku.tax_category_rule_id = tax_rule_id
        sku.raw = {**(sku.raw or {}), "managedLocally": True, "masterImport": filename}
        if item["category"] or _has(headers, ("品类", "商品品类")):
            sku.raw["goodsCategory"] = item["category"]
            if sku.product_id:
                product = db.get(Product, sku.product_id)
                if product is not None:
                    product.category = item["category"]
                    product.raw = {**(product.raw or {}), "categoryLocallyEdited": True}
        db.flush()
        item["sku"] = sku

    for item in [spec for spec in specs if spec["kind"] == "consumable"]:
        consumable = db.get(Consumable, item["id"]) if item["id"] is not None else None
        if item["id"] is not None and consumable is None:
            raise ValueError(f"第 {_row_no(item['row'])} 行耗材档案ID不存在：{item['id']}")
        if consumable is None:
            consumable = db.query(Consumable).filter(Consumable.code == item["code"]).first()
        if consumable is None:
            consumable = Consumable(code=item["code"])
            db.add(consumable)
            created += 1
        else:
            updated += 1
        code_conflict = db.query(Consumable).filter(Consumable.code == item["code"])
        if consumable.id is not None:
            code_conflict = code_conflict.filter(Consumable.id != consumable.id)
        if code_conflict.first():
            raise ValueError(f"耗材编码已存在：{item['code']}")
        consumable.code = item["code"]
        consumable.name = item["name"]
        consumable.barcode = item["barcode"]
        consumable.category = item["category"]
        consumable.unit = item["unit"]
        consumable.purchase_unit_cost = item["purchase_cost"]
        consumable.min_stock_qty = item["min_stock"] if item["min_stock"] is not None else Decimal("0")
        consumable.status = item["status"]
        tax_code, tax_rule_id, has_tax = item["tax"]
        if has_tax:
            consumable.tax_code = tax_code
            consumable.tax_category_rule_id = tax_rule_id
        consumable.raw = {**(consumable.raw or {}), "masterImport": filename}
        db.flush()
        if item["links"] is not None:
            wanted: dict[int, ProductSku] = {}
            for ref in item["links"]:
                sku = _find_sku(db, ref)
                if sku is None:
                    raise ValueError(f"第 {_row_no(item['row'])} 行关联正品 SKU 不存在：{ref}")
                wanted[sku.id] = sku
            existing = {
                mapping.sku_id: mapping
                for mapping in db.query(ConsumableSkuMapping).filter_by(consumable_id=consumable.id).all()
            }
            for sku_id, mapping in existing.items():
                if sku_id not in wanted:
                    db.delete(mapping)
                    mappings_removed += 1
                    affected_skus.add(sku_id)
            for sku_id in wanted:
                affected_skus.add(sku_id)
                if sku_id not in existing:
                    db.add(ConsumableSkuMapping(
                        consumable_id=consumable.id,
                        sku_id=sku_id,
                        usage_per_unit=Decimal("1"),
                        note="基础档案模板导入",
                    ))
                    mappings_created += 1

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    audit(db, actor, f"master_data.{dataset}.import", "product_skus" if dataset == "bundles" else "catalog", None, {
        "fileName": filename, "sheet": sheet, "rows": len(specs),
        "created": created, "updated": updated,
        "mappingsCreated": mappings_created, "mappingsRemoved": mappings_removed,
    })
    return {
        "dataset": dataset, "sheet": sheet, "rows": len(specs), "skipped": skipped,
        "created": created, "updated": updated,
        "mappingsCreated": mappings_created, "mappingsRemoved": mappings_removed,
        "ignoredInventoryFields": ["当前库存", "工厂库存", "在途数量", "已用数量"],
    }


def import_bundles(db: Session, content: bytes, filename: str, actor: str) -> dict[str, Any]:
    return import_catalog(db, content, filename, actor, scope="bundles")


def import_warehouses(db: Session, content: bytes, filename: str, actor: str) -> dict[str, Any]:
    headers, rows, sheet = _read_table(content, filename, ("档案ID", "仓库编号", "仓库编码", "仓库名称"))
    if not _has(headers, ("仓库编号", "仓库编码")) or not _has(headers, ("仓库名称", "仓库名")):
        raise ValueError("仓库档案必须包含“仓库编号”和“仓库名称”列")
    specs: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    seen_external: set[str] = set()
    for row in rows:
        code = _value(row, ("仓库编号", "仓库编码"))
        name = _value(row, ("仓库名称", "仓库名"))
        if not code and not name:
            continue
        if not code or not name:
            raise ValueError(f"第 {_row_no(row)} 行仓库编号和名称不能为空")
        code = code.upper()
        if code in seen_codes:
            raise ValueError(f"第 {_row_no(row)} 行仓库编码重复：{code}")
        seen_codes.add(code)
        external = _value(row, ("吉客云仓库ID", "吉客云仓库 ID"))
        if external and external in seen_external:
            raise ValueError(f"第 {_row_no(row)} 行吉客云仓库ID重复：{external}")
        if external:
            seen_external.add(external)
        warehouse_type = _value(row, ("类型", "仓库类型")).lower()
        warehouse_type = {"工厂仓": "factory", "b2c仓": "b2c", "b2c": "b2c", "其他": "other", "other": "other", "factory": "factory"}.get(warehouse_type, warehouse_type or "other")
        purpose = _value(row, ("用途", "库存用途")).lower()
        purpose = {"正品+耗材": "both", "正品 + 耗材": "both", "正品": "goods", "仅正品": "goods", "耗材": "consumable", "仅耗材": "consumable", "both": "both", "goods": "goods", "consumable": "consumable"}.get(purpose, purpose or "both")
        specs.append({
            "row": row,
            "id": _int_row(row, ("档案ID", "仓库ID"), "档案ID"),
            "code": code,
            "name": name,
            "warehouse_type": warehouse_type,
            "purpose": purpose,
            "external": external or None,
            "is_sellable": _bool_row(row, ("参与可售", "可售"), "参与可售"),
            "status": _status(_value(row, ("状态",)), row),
            "note": _value(row, ("备注",)),
        })
    created = updated = 0
    for item in specs:
        values = warehouse_service._normalize(
            code=item["code"], name=item["name"], warehouse_type=item["warehouse_type"],
            purpose=item["purpose"], is_sellable=item["is_sellable"], status=item["status"],
            note=item["note"], jackyun_warehouse_id=item["external"],
        )
        warehouse = db.get(Warehouse, item["id"]) if item["id"] is not None else None
        if item["id"] is not None and warehouse is None:
            raise ValueError(f"第 {_row_no(item['row'])} 行仓库档案ID不存在：{item['id']}")
        if warehouse is None:
            warehouse = db.query(Warehouse).filter(Warehouse.code == values["code"]).first()
        if warehouse is None:
            warehouse_service._check_unique(db, values)
            db.add(Warehouse(**values, raw={"masterImport": filename}))
            created += 1
            continue
        warehouse_service._check_unique(db, values, row_id=warehouse.id)
        for key, value in values.items():
            setattr(warehouse, key, value)
        warehouse.raw = {**(warehouse.raw or {}), "masterImport": filename}
        updated += 1
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    audit(db, actor, "master_data.warehouses.import", "warehouses", None, {"fileName": filename, "rows": len(specs), "created": created, "updated": updated})
    return {"dataset": "warehouses", "sheet": sheet, "rows": len(specs), "created": created, "updated": updated}


def import_suppliers(db: Session, content: bytes, filename: str, actor: str) -> dict[str, Any]:
    headers, rows, sheet = _read_table(content, filename, ("供应商ID", "供应商名称", "税号", "联系人"))
    if not _has(headers, ("供应商名称", "名称")):
        raise ValueError("供应商档案必须包含“供应商名称”列")
    specs: list[dict[str, Any]] = []
    seen_tax: set[str] = set()
    for row in rows:
        name = _value(row, ("供应商名称", "名称"))
        if not name:
            raise ValueError(f"第 {_row_no(row)} 行供应商名称不能为空")
        tax_no = _value(row, ("税号", "统一社会信用代码")).upper()
        if tax_no:
            if tax_no in seen_tax:
                raise ValueError(f"第 {_row_no(row)} 行税号在文件中重复：{tax_no}")
            seen_tax.add(tax_no)
        specs.append({
            "row": row,
            "id": _int_row(row, ("供应商ID", "档案ID"), "供应商ID"),
            "name": name,
            "platform": _value(row, ("平台", "渠道")) or "1688",
            "external_shop_id": _value(row, ("店铺/供应商编码", "店铺 / 供应商编码", "店铺编码")),
            "contact": _value(row, ("联系人",)),
            "tax_no": tax_no,
            "phone": _value(row, ("电话", "手机号")),
            "address": _value(row, ("地址",)),
            "notes": _value(row, ("备注",)),
            "is_temp": _bool_row(row, ("临时供应商", "是否临时"), "临时供应商"),
        })
    created = updated = 0
    for item in specs:
        supplier = db.get(Supplier, item["id"]) if item["id"] is not None else None
        if item["id"] is not None and supplier is None:
            raise ValueError(f"第 {_row_no(item['row'])} 行供应商ID不存在：{item['id']}")
        if supplier is None and item["tax_no"]:
            matches = db.query(Supplier).filter(Supplier.tax_no == item["tax_no"]).all()
            if len(matches) > 1:
                raise ValueError(f"税号 {item['tax_no']} 对应多个供应商")
            supplier = matches[0] if matches else None
        if supplier is None:
            matches = db.query(Supplier).filter(
                Supplier.name == item["name"], Supplier.platform == item["platform"]
            ).all()
            if len(matches) > 1:
                raise ValueError(f"供应商 {item['name']} 存在多个同名档案，请在模板中填写供应商ID")
            supplier = matches[0] if matches else None
        tax_conflict = db.query(Supplier).filter(Supplier.tax_no == item["tax_no"], Supplier.tax_no != "")
        if supplier is not None:
            tax_conflict = tax_conflict.filter(Supplier.id != supplier.id)
        if item["tax_no"] and tax_conflict.first():
            raise ValueError(f"税号已被其他供应商使用：{item['tax_no']}")
        if supplier is None:
            supplier = Supplier(name=item["name"])
            db.add(supplier)
            created += 1
        else:
            updated += 1
        supplier.name = item["name"]
        supplier.platform = item["platform"]
        supplier.external_shop_id = item["external_shop_id"]
        supplier.contact = item["contact"]
        supplier.tax_no = item["tax_no"]
        supplier.phone = item["phone"]
        supplier.address = item["address"]
        supplier.notes = item["notes"]
        supplier.is_temp = item["is_temp"]
        db.flush()
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    audit(db, actor, "master_data.suppliers.import", "suppliers", None, {"fileName": filename, "rows": len(specs), "created": created, "updated": updated})
    return {"dataset": "suppliers", "sheet": sheet, "rows": len(specs), "created": created, "updated": updated}


def import_external_orders(db: Session, content: bytes, filename: str, actor: str) -> dict[str, Any]:
    headers, rows, sheet = _read_table(content, filename, ("订单ID", "订单号", "外部订单号", "渠道", "供应商"))
    if not _has(headers, ("订单号", "外部订单号")):
        raise ValueError("其他渠道采购订单必须包含“订单号”列")
    specs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        order_no = _value(row, ("订单号", "外部订单号"))
        if not order_no:
            raise ValueError(f"第 {_row_no(row)} 行订单号不能为空")
        platform = purchase_service.normalize_platform(_value(row, ("渠道", "平台")) or "other")
        key = (platform, order_no)
        if key in seen:
            raise ValueError(f"第 {_row_no(row)} 行订单号在文件中重复：{platform}/{order_no}")
        seen.add(key)
        order_amount = _decimal_row(row, ("订单金额",), "订单金额")
        paid_amount = _decimal_row(row, ("实付金额",), "实付金额")
        if (order_amount is not None and order_amount < 0) or (paid_amount is not None and paid_amount < 0):
            raise ValueError(f"第 {_row_no(row)} 行金额不能为负数")
        specs.append({
            "row": row,
            "id": _int_row(row, ("订单ID", "档案ID"), "订单ID"),
            "order_no": order_no,
            "platform": platform,
            "supplier": _value(row, ("供应商/往来单位", "供应商", "供应商/工厂")),
            "title": _value(row, ("备注", "标题")),
            "ordered_at": _datetime_row(row, ("采购时间", "下单时间", "下单日期"), "采购时间"),
            "order_amount": order_amount,
            "paid_amount": paid_amount,
            "buyer_account": _value(row, ("买家账号", "采购账号")),
            "order_status": _value(row, ("订单状态",)),
        })
    created = updated = 0
    changed: list[ExternalPurchaseOrder] = []
    for item in specs:
        order = db.get(ExternalPurchaseOrder, item["id"]) if item["id"] is not None else None
        if item["id"] is not None and order is None:
            raise ValueError(f"第 {_row_no(item['row'])} 行订单ID不存在：{item['id']}")
        if order is None:
            order = db.query(ExternalPurchaseOrder).filter_by(
                platform=item["platform"], external_order_id=item["order_no"]
            ).first()
        conflict = db.query(ExternalPurchaseOrder).filter_by(
            platform=item["platform"], external_order_id=item["order_no"]
        )
        if order is not None:
            conflict = conflict.filter(ExternalPurchaseOrder.id != order.id)
        if conflict.first():
            raise ValueError(f"订单号已存在：{item['platform']}/{item['order_no']}")
        if order is None:
            order = ExternalPurchaseOrder(
                external_order_id=item["order_no"],
                platform=item["platform"],
                purchase_status="pending_refine",
                invoice_status="unverified",
                raw={"source": "master_import", "sourceFile": filename},
            )
            db.add(order)
            created += 1
        else:
            updated += 1
        order.external_order_id = item["order_no"]
        order.platform = item["platform"]
        order.supplier_name = item["supplier"]
        order.title = item["title"]
        order.ordered_at = item["ordered_at"]
        order.order_amount = item["order_amount"]
        order.paid_amount = item["paid_amount"]
        order.buyer_account = item["buyer_account"]
        if item["order_status"]:
            order.order_status = item["order_status"]
        order.raw = {**(order.raw or {}), "source": "master_import", "sourceFile": filename}
        db.flush()
        changed.append(order)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    # 已有入库对照的订单在批量建档后补齐分配行；不在这里做启发式强匹配。
    from app.services.inbound_allocation_seed import seed_allocations_for_po

    for order in changed:
        seed_allocations_for_po(db, order)
    audit(db, actor, "master_data.external_orders.import", "external_purchase_orders", None, {"fileName": filename, "rows": len(specs), "created": created, "updated": updated})
    return {"dataset": "external_orders", "sheet": sheet, "rows": len(specs), "created": created, "updated": updated}


def _rule_match_mode(value: str, row: dict[str, Any]) -> str:
    raw = value.strip().lower()
    modes = {
        "": "contains", "包含": "contains", "contains": "contains",
        "精确": "exact", "完全匹配": "exact", "exact": "exact",
        "前缀": "prefix", "prefix": "prefix",
    }
    if raw not in modes:
        raise ValueError(f"第 {_row_no(row)} 行匹配方式只能填写包含、精确或前缀")
    return modes[raw]


def import_tax_rules(db: Session, content: bytes, filename: str, actor: str) -> dict[str, Any]:
    headers, rows, sheet = _read_table(
        content,
        filename,
        ("规则ID", "分类规则", "财务大类", "项目名称", "税务代码", "匹配关键词"),
    )
    has_pattern = _has(headers, ("分类规则", "规则", "匹配规则"))
    has_parts = _has(headers, ("财务大类", "大类", "分类")) and _has(headers, ("项目名称", "项目", "货品项目"))
    if not has_pattern and not has_parts:
        raise ValueError("财务分类模板必须包含“分类规则”列，或同时包含“财务大类”和“项目名称”列")

    specs: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    seen_ids: set[int] = set()
    for row in rows:
        rule_id = _int_row(row, ("规则ID", "分类ID", "财务分类ID"), "规则ID")
        if rule_id is not None:
            if rule_id in seen_ids:
                raise ValueError(f"第 {_row_no(row)} 行规则ID在文件中重复：{rule_id}")
            seen_ids.add(rule_id)
        pattern = _value(row, ("分类规则", "规则", "匹配规则")) if has_pattern else ""
        if pattern:
            try:
                category, item = tax_category_rule_service.parse_pattern(pattern)
            except ValueError as exc:
                raise ValueError(f"第 {_row_no(row)} 行分类规则格式错误：{exc}") from exc
        else:
            category = _value(row, ("财务大类", "大类", "分类"))
            item = _value(row, ("项目名称", "项目", "货品项目"))
            if not category or not item:
                raise ValueError(f"第 {_row_no(row)} 行财务大类和项目名称不能为空")
        key = (category.casefold(), item.casefold())
        if key in seen_keys:
            raise ValueError(f"第 {_row_no(row)} 行分类规则重复：{category}/{item}")
        seen_keys.add(key)
        tax_code = tax_category_rule_service.normalize_tax_code(_value(row, ("税务代码", "税收分类编码")))
        match_keyword = _value(row, ("匹配关键词", "匹配关键字", "关键词")) or item
        match_mode = _rule_match_mode(_value(row, ("匹配方式", "匹配模式")), row)
        priority = _int_row(row, ("优先级", "排序"), "优先级") if _has(headers, ("优先级", "排序")) else 100
        if priority is None:
            priority = 100
        if priority < 0 or priority > 9999:
            raise ValueError(f"第 {_row_no(row)} 行优先级必须在 0 到 9999 之间")
        enabled = _bool_row(row, ("启用", "是否启用"), "启用", default=True) if _has(headers, ("启用", "是否启用")) else True
        specs.append({
            "row": row,
            "id": rule_id,
            "category": category,
            "item": item,
            "tax_code": tax_code,
            "match_keyword": match_keyword,
            "match_mode": match_mode,
            "priority": priority,
            "enabled": enabled,
            "note": _value(row, ("备注", "说明")),
        })

    created = updated = linked_products = linked_consumables = 0
    for item in specs:
        rule = db.get(TaxAccountingCategoryRule, item["id"]) if item["id"] is not None else None
        if item["id"] is not None and rule is None:
            raise ValueError(f"第 {_row_no(item['row'])} 行规则ID不存在：{item['id']}")
        if rule is None:
            matches = db.query(TaxAccountingCategoryRule).filter_by(
                category_name=item["category"], item_name=item["item"]
            ).all()
            if len(matches) > 1:
                raise ValueError(f"分类规则 {item['category']}/{item['item']} 存在多个档案")
            rule = matches[0] if matches else None
        duplicate = db.query(TaxAccountingCategoryRule).filter(
            TaxAccountingCategoryRule.category_name == item["category"],
            TaxAccountingCategoryRule.item_name == item["item"],
        )
        if rule is not None:
            duplicate = duplicate.filter(TaxAccountingCategoryRule.id != rule.id)
        if duplicate.first():
            raise ValueError(f"分类规则已存在：{item['category']}/{item['item']}")
        if rule is None:
            rule = TaxAccountingCategoryRule(
                category_name=item["category"],
                item_name=item["item"],
                created_by=actor or "system",
            )
            db.add(rule)
            created += 1
        else:
            updated += 1
        rule.category_name = item["category"]
        rule.item_name = item["item"]
        rule.tax_code = item["tax_code"]
        rule.match_keyword = item["match_keyword"]
        rule.match_mode = item["match_mode"]
        rule.priority = item["priority"]
        rule.enabled = item["enabled"]
        rule.note = item["note"]
        rule.updated_by = actor or "system"
        db.flush()
        products_before = db.query(ProductSku).filter(ProductSku.tax_category_rule_id == rule.id).count()
        consumables_before = db.query(Consumable).filter(Consumable.tax_category_rule_id == rule.id).count()
        db.query(ProductSku).filter(ProductSku.tax_category_rule_id == rule.id).update(
            {ProductSku.tax_code: rule.tax_code or ""}, synchronize_session=False
        )
        db.query(Consumable).filter(Consumable.tax_category_rule_id == rule.id).update(
            {Consumable.tax_code: rule.tax_code or ""}, synchronize_session=False
        )
        linked_products += products_before
        linked_consumables += consumables_before
        linked = tax_category_rule_service.link_unambiguous_catalog_rows(db, rule)
        linked_products += linked["products"]
        linked_consumables += linked["consumables"]

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    audit(db, actor, "master_data.tax_rules.import", "tax_accounting_category_rules", None, {
        "fileName": filename, "sheet": sheet, "rows": len(specs),
        "created": created, "updated": updated,
        "linkedProducts": linked_products, "linkedConsumables": linked_consumables,
    })
    return {
        "dataset": "tax_rules", "sheet": sheet, "rows": len(specs),
        "created": created, "updated": updated,
        "linkedProducts": linked_products, "linkedConsumables": linked_consumables,
    }


def import_dataset(db: Session, dataset: str, content: bytes, filename: str, actor: str) -> dict[str, Any]:
    if dataset not in DATASETS:
        raise ValueError("不支持的基础档案导入类型")
    if dataset == "catalog":
        return import_catalog(db, content, filename, actor)
    if dataset == "bundles":
        return import_bundles(db, content, filename, actor)
    if dataset == "tax_rules":
        return import_tax_rules(db, content, filename, actor)
    if dataset == "warehouses":
        return import_warehouses(db, content, filename, actor)
    if dataset == "suppliers":
        return import_suppliers(db, content, filename, actor)
    return import_external_orders(db, content, filename, actor)
