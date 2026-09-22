"""销售出库报表（财务月度自动化）：从吉客云出库单（JackyunGoodsDocument, document_type='outbound'）聚合。

口径约定（用户 2026-09-06 拍板）：
- 吉客云「销售出库单」API（getGoodsDocOutListInfo）只返回 数量/货品/仓库/日期，不返金额——
  所以本报表默认聚合「系统里有什么就拉什么」：出库数量、按货品、按仓库、按公司。
- 金额维度（amount_tax 等）仅在用户上传吉客云销售出库导出 Excel 后由文件导入通道补全；
  届时报表自动识别 hasAmount 并展示金额列，无需改码。
- 交付双形态：实时查询页（/sales-outbound）+ 每月定时归档进财务资料中心（category=jackyun）。
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
from typing import Any

from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import settings
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.services.monthly_core import month_bounds

DOC_TYPE = "outbound"


def months_with_data(db: Session) -> list[dict]:
    """列出有出库单数据的业务账期（Asia/Shanghai），降序。"""
    timestamps = (
        db.query(JackyunGoodsDocument.document_at)
        .filter(
            JackyunGoodsDocument.document_type == DOC_TYPE,
            JackyunGoodsDocument.document_at.isnot(None),
        )
        .all()
    )
    tz = ZoneInfo(settings.TZ)
    counts: dict[tuple[int, int], int] = {}
    for (value,) in timestamps:
        local = value.astimezone(tz) if value.tzinfo is not None else value.replace(tzinfo=tz)
        key = (local.year, local.month)
        counts[key] = counts.get(key, 0) + 1
    return [
        {"year": year, "month": month, "docCount": counts[(year, month)]}
        for year, month in sorted(counts, reverse=True)
    ]


def build_report(db: Session, year: int, month: int) -> dict[str, Any]:
    """聚合单月销售出库：汇总 + 按仓库 + 按货品 + 明细。金额列在 hasAmount 为真时才填充。"""
    start, nxt = month_bounds(year, month)
    docs = (
        db.query(JackyunGoodsDocument)
        .filter(
            JackyunGoodsDocument.document_type == DOC_TYPE,
            JackyunGoodsDocument.document_at >= start,
            JackyunGoodsDocument.document_at < nxt,
        )
        .order_by(JackyunGoodsDocument.document_at)
        .all()
    )

    by_sku: dict[str, dict[str, Any]] = {}
    by_wh: dict[str, dict[str, Any]] = {}
    items_rows: list[dict[str, Any]] = []
    total_qty = Decimal("0")
    total_amt = Decimal("0")
    has_amount = False

    for d in docs:
        wh_name = d.warehouse_name or d.warehouse_code or "—"
        wh_code = d.warehouse_code or ""
        wh = by_wh.setdefault(
            wh_name,
            {"warehouseName": wh_name, "warehouseCode": wh_code, "docCount": 0,
             "quantity": Decimal("0"), "amount": Decimal("0")},
        )
        wh["docCount"] += 1

        items = (
            db.query(JackyunGoodsDocumentItem)
            .filter_by(document_id=d.id)
            .order_by(JackyunGoodsDocumentItem.line_no)
            .all()
        )
        for it in items:
            qty = it.quantity or Decimal("0")
            amt = it.amount_tax  # Excel 导入补全后才有值；否则 None
            if amt is not None:
                has_amount = True
            total_qty += qty
            if amt is not None:
                total_amt += amt
            wh["quantity"] += qty
            if amt is not None:
                wh["amount"] += amt

            key = it.goods_no or it.sku_barcode or "未知货号"
            sku = by_sku.setdefault(
                key,
                {"goodsNo": it.goods_no or "", "goodsName": it.goods_name or "",
                 "quantity": Decimal("0"), "docCount": 0, "amount": Decimal("0")},
            )
            sku["quantity"] += qty
            sku["docCount"] += 1
            if amt is not None:
                sku["amount"] += amt

            items_rows.append({
                "goodsdocNo": d.goodsdoc_no,
                "documentAt": d.document_at.isoformat() if d.document_at else "",
                "warehouseName": wh_name,
                "companyName": d.company_name or "",
                "goodsNo": it.goods_no or "",
                "skuBarcode": it.sku_barcode or "",
                "goodsName": it.goods_name or "",
                "quantity": qty,
                "unitName": it.unit_name or "",
                "amountTax": (str(amt) if amt is not None else ""),
            })

    by_sku_list = sorted(by_sku.values(), key=lambda x: x["quantity"], reverse=True)
    by_wh_list = sorted(by_wh.values(), key=lambda x: x["quantity"], reverse=True)

    return {
        "year": year,
        "month": month,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "hasAmount": has_amount,
        "summary": {
            "docCount": len(docs),
            "totalQuantity": str(total_qty),
            "totalAmount": (str(total_amt) if has_amount else None),
            "skuCount": len(by_sku),
            "warehouseCount": len(by_wh),
        },
        "byWarehouse": [
            {
                "warehouseName": w["warehouseName"],
                "warehouseCode": w["warehouseCode"],
                "docCount": w["docCount"],
                "quantity": str(w["quantity"]),
                "amount": (str(w["amount"]) if has_amount else None),
            }
            for w in by_wh_list
        ],
        "bySku": [
            {
                "goodsNo": s["goodsNo"],
                "goodsName": s["goodsName"],
                "quantity": str(s["quantity"]),
                "docCount": s["docCount"],
                "amount": (str(s["amount"]) if has_amount else None),
            }
            for s in by_sku_list
        ],
        "items": items_rows,
    }


def to_csv(report: dict[str, Any]) -> bytes:
    """明细级 CSV（utf-8-sig，Excel 直接可读）。一条出库单的每一行明细一行。"""
    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(["账期", f"{report['year']}-{report['month']:02d}"])
    w.writerow(["出库单号", "出库日期", "仓库", "公司", "货号", "条码",
                "品名", "数量", "单位", "金额(含税)"])
    for it in report.get("items", []):
        w.writerow([
            it["goodsdocNo"],
            (it["documentAt"] or "")[:10],
            it["warehouseName"],
            it["companyName"],
            it["goodsNo"],
            it["skuBarcode"],
            it["goodsName"],
            it["quantity"],
            it["unitName"],
            it["amountTax"],
        ])
    return buf.getvalue().encode("utf-8-sig")
