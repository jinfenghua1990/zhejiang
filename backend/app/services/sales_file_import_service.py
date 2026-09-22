"""吉客云《销售单查询》Excel → 本地销售业绩数据源（sales_orders / sales_order_items）。

背景（用户口径，2026-09-07 拍板）：
- 吉客云开放平台 API 有到期风险；到期后销售业绩改由用户从吉客云客户端导出
  《销售单查询》（含「销售单」+「销售单货品」两个 sheet）手工导入本通道。
- 金额/业绩维度一律以用户上传表格为准（与采购侧「入库申请单货品」口径一致）。
- 成交口径仍由 services/sales_scope 唯一决定；关闭/取消/作废/待审核/退货/退款
  保留为历史业务事实，但不会进入业绩聚合，也不会物理删除已有订单。
- 幂等：按 JY 订单号 upsert，重复导入覆盖更新；订单明细整单替换。

时间口径：吉客云导出的「处理时间」是相对时长（如 15小时44分钟），不可用；
付款时间 是唯一可靠绝对时间 → ordered_at / paid_at 一律取付款时间，缺省回退发货时间。
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.adapters.bank_file import sanitize_name
from app.config import settings
from app.core.audit import audit
from app.models.catalog import ProductSku
from app.models.sales import SalesOrder, SalesOrderItem
from app.services.inbound_cost_service import sales_sku_lookup
from app.services.sales_scope import is_deal_status

MAX_FILE_BYTES = 100 * 1024 * 1024

# 销售单主表：吉客云导出列名 → 模型字段
_ORDER_KEYS = ("订单编号",)
_STATUS_KEYS = ("订单状态",)
_CHANNEL_KEYS = ("销售渠道", "渠道", "店铺", "店铺名称")
_TYPE_KEYS = ("订单类型",)
_SETTLE_KEYS = ("结算状态",)
_ORDERTIME_KEYS = ("下单时间", "订单时间", "创建时间", "拍下时间")
_PAYTIME_KEYS = ("付款时间",)
_SHIPTIME_KEYS = ("发货时间", "出库时间")
_AMOUNT_KEYS = ("应收合计", "订单金额", "应付金额")
_PAID_KEYS = ("实付金额", "实收金额", "成交金额", "金额")
_COST_KEYS = ("订单货品成本", "成本")
_GROSS_KEYS = ("毛利",)
_QTY_KEYS = ("货品数量", "数量")
_WAREHOUSE_KEYS = ("发货仓库",)
_NETNO_KEYS = ("网店订单号", "平台单号", "外部单号", "原始单号")
_LOGISTICS_KEYS = ("物流单号",)
_EXPRESS_KEYS = ("物流公司",)

# 销售单货品：明细列名 → 模型字段
_ITEM_ORDER_KEYS = ("订单编号",)
_ITEM_SKU_KEYS = ("货品编号", "货品编码", "商品编号", "商品编码", "SKU编码", "SKU", "货号", "商品货号", "商家编码")
_ITEM_NAME_KEYS = ("货品名称", "商品名称", "品名", "商品")
_ITEM_QTY_KEYS = ("数量", "基本数量", "货品数量", "实发数量")
_ITEM_PRICE_KEYS = ("单价", "成交单价", "销售单价")
_ITEM_AMOUNT_KEYS = ("金额", "行金额", "销售额", "成交金额", "商品金额")
_ITEM_DISCOUNT_KEYS = ("优惠", "优惠金额")
_ITEM_SPEC_KEYS = ("规格", "规格名称")
_ITEM_UNIT_KEYS = ("单位", "基本单位")
_ITEM_GIFT_KEYS = ("赠品",)

# 不参与业绩聚合的状态统一定义在 sales_scope（成交口径唯一来源）

_ts_fmts = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d")


def _cell(row: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for k in aliases:
        v = row.get(k)
        if v is not None and str(v).strip() != "":
            return v
    return None


def _num(value: Any) -> Decimal | None:
    """金额/数量统一用 Decimal，禁止二进制 float 进入业务字段。"""
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("¥", "")
    if not text:
        return None
    try:
        return Decimal(text).quantize(Decimal("0.0001"))
    except (InvalidOperation, ValueError):
        return None


def _dt(value: Any) -> datetime | None:
    """吉客云时间统一解释为 Asia/Shanghai，并返回 aware datetime。"""
    if value is None:
        return None
    tz = ZoneInfo(settings.TZ)
    if isinstance(value, datetime):
        return value.astimezone(tz) if value.tzinfo else value.replace(tzinfo=tz)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=tz)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            parsed = from_excel(value)
            return parsed.astimezone(tz) if parsed.tzinfo else parsed.replace(tzinfo=tz)
        except (TypeError, ValueError, OverflowError):
            return None
    text = str(value).strip()
    if not text or "小时" in text or "分钟" in text:
        return None
    for fmt in _ts_fmts:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    return None


def _row_headers(ws) -> dict[str, int]:
    for row in ws.iter_rows(values_only=True):
        headers = {str(c).strip(): i for i, c in enumerate(row) if c is not None}
        if len(headers) >= 3:
            return headers
    return {}


def _has_header(headers: dict[str, int], aliases: tuple[str, ...]) -> bool:
    return any(alias in headers for alias in aliases)


def _is_order_sheet(headers: dict[str, int]) -> bool:
    return (
        _has_header(headers, _ORDER_KEYS)
        and _has_header(headers, _CHANNEL_KEYS)
        and _has_header(headers, _STATUS_KEYS)
    )


def _is_item_sheet(headers: dict[str, int]) -> bool:
    """支持独立明细 Sheet，也支持订单主表中展开的明细列。"""
    return _has_header(headers, _ITEM_ORDER_KEYS) and (
        _has_header(headers, _ITEM_SKU_KEYS)
        or (_has_header(headers, _ITEM_NAME_KEYS) and _has_header(headers, _ITEM_QTY_KEYS))
    )


def _has_item_data(payload: dict[str, Any]) -> bool:
    return bool(
        _cell(payload, _ITEM_SKU_KEYS)
        or (_cell(payload, _ITEM_NAME_KEYS) and _cell(payload, _ITEM_QTY_KEYS))
    )


def _archive_upload(content: bytes, original_name: str, sha: str) -> Path:
    """保留网页实际收到的原件，便于复核解析结果；同一指纹不重复覆盖。"""
    root = Path(settings.DATA_DIR).resolve() / "sales-imports"
    root.mkdir(parents=True, exist_ok=True)
    clean_name = sanitize_name(original_name or "sales-list.xlsx")
    target = root / f"{sha}_{clean_name}"
    try:
        with target.open("xb") as output:
            output.write(content)
    except FileExistsError:
        pass
    return target


def _collect_item_groups(ws, headers: dict[str, int], *, same_sheet: bool) -> dict[str, list[dict[str, Any]]]:
    """读取独立明细 Sheet，或主表中按行展开的明细。"""
    groups: dict[str, list[dict[str, Any]]] = {}
    current_order_no = ""
    for row in ws.iter_rows(values_only=True):
        if not row:
            continue
        payload = {name: row[i] for name, i in headers.items() if i < len(row)}
        order_no = str(_cell(payload, _ITEM_ORDER_KEYS) or "").strip()
        if order_no == "订单编号":
            continue
        has_item_data = _has_item_data(payload)
        if order_no:
            current_order_no = order_no
        elif same_sheet and has_item_data:
            # Excel 合并单元格在后续行读出来是 None，沿用上一行订单号。
            order_no = current_order_no
        if not order_no or not has_item_data:
            continue
        groups.setdefault(order_no, []).append(payload)
    return groups


def _is_valid_status(status: str | None) -> bool:
    return is_deal_status(status)


def import_sales_file(
    db: Session,
    *,
    content: bytes,
    original_name: str,
    actor: str,
) -> dict:
    """导入吉客云《销售单查询》导出：销售单 + 销售单货品 双 sheet。返回统计。"""
    if not content:
        raise ValueError("空文件")
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("文件超过 100 MiB 上限")

    sha = hashlib.sha256(content).hexdigest()[:16]
    archive_path = _archive_upload(content, original_name, sha)
    try:
        wb = load_workbook(BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError("文件格式无法解析，请上传有效的 .xlsx 文件") from exc
    order_ws = None
    item_ws = None
    sheet_diagnostics: list[dict[str, Any]] = []
    for name in wb.sheetnames:
        ws = wb[name]
        hd = _row_headers(ws)
        is_order = _is_order_sheet(hd)
        is_item = _is_item_sheet(hd)
        sheet_diagnostics.append({
            "name": name,
            "role": "order+item" if is_order and is_item else "order" if is_order else "item" if is_item else "unknown",
            "headers": list(hd.keys()),
        })
        if order_ws is None and is_order:
            order_ws = (ws, hd)
        if item_ws is None and is_item:
            item_ws = (ws, hd)
    if order_ws is None:
        wb.close()
        raise ValueError("未识别到「销售单」sheet（需要含 订单编号/销售渠道/订单状态 列）")
    if item_ws is None:
        wb.close()
        raise ValueError("未识别到销售明细列，请确认文件包含货品编号（或商品编码）、货品名称和数量")

    # ---------- 货品档案校验：明细货品编码必须已建档，否则整单拒绝（不写任何数据） ----------
    iws, ihd = item_ws
    same_sheet_items = iws is order_ws[0]
    item_groups = _collect_item_groups(iws, ihd, same_sheet=same_sheet_items)
    known_codes = {
        str(code).strip().lower()
        for (code,) in db.query(ProductSku.sku_code).all()
        if code is not None and str(code).strip()
    }
    missing_codes: list[str] = []
    for lines in item_groups.values():
        for line in lines:
            code = str(_cell(line, _ITEM_SKU_KEYS) or "").strip()
            if (not code or code.lower() not in known_codes) and code not in missing_codes:
                missing_codes.append(code)
    if missing_codes:
        wb.close()
        raise ValueError(
            "货品档案缺少以下货品编码，请先建档或修正后再导入：" + ",".join(missing_codes)
        )

    imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ---------- sheet1 销售单：upsert 有效订单 ----------
    ws, hd = order_ws
    valid_rows: list[dict] = []
    cancelled_nos: set[str] = set()
    cancelled_payloads: dict[str, dict[str, Any]] = {}
    order_idx = 0
    for row in ws.iter_rows(values_only=True):
        if not row:
            continue
        payload = {name: row[i] for name, i in hd.items() if i < len(row)}
        order_no = str(_cell(payload, _ORDER_KEYS) or "").strip()
        # 跳过表头重复行/合计等垃圾行：订单号列等于列名、或非 JY 单号前缀
        if not order_no or order_no == "订单编号" or not order_no.startswith("JY"):
            continue
        status = str(_cell(payload, _STATUS_KEYS) or "").strip()
        if not _is_valid_status(status):
            cancelled_nos.add(order_no)
            cancelled_payloads[order_no] = payload
            continue
        valid_rows.append(payload)
        order_idx += 1

    seen: set[str] = set()
    created = updated = 0
    for payload in valid_rows:
        order_no = str(_cell(payload, _ORDER_KEYS)).strip()
        if order_no in seen:
            continue
        seen.add(order_no)
        order_time = (
            _dt(_cell(payload, _ORDERTIME_KEYS))
            or _dt(_cell(payload, _PAYTIME_KEYS))
            or _dt(_cell(payload, _SHIPTIME_KEYS))
        )
        pay_at = _dt(_cell(payload, _PAYTIME_KEYS)) or order_time
        row = db.query(SalesOrder).filter_by(order_no=order_no).first()
        raw = {
            "netOrderNo": str(_cell(payload, _NETNO_KEYS) or ""),
            "orderStatus": str(_cell(payload, _STATUS_KEYS) or ""),
            "settleStatus": str(_cell(payload, _SETTLE_KEYS) or ""),
            "warehouse": str(_cell(payload, _WAREHOUSE_KEYS) or ""),
            "logisticsNo": str(_cell(payload, _LOGISTICS_KEYS) or ""),
            "logisticsCompany": str(_cell(payload, _EXPRESS_KEYS) or ""),
            "goodsCount": str(_num(_cell(payload, _QTY_KEYS))) if _num(_cell(payload, _QTY_KEYS)) is not None else None,
            "goodsCost": str(_num(_cell(payload, _COST_KEYS))) if _num(_cell(payload, _COST_KEYS)) is not None else None,
            "grossProfit": str(_num(_cell(payload, _GROSS_KEYS))) if _num(_cell(payload, _GROSS_KEYS)) is not None else None,
            "_source": "jky_sales_file",
            "_file": original_name,
            "_sha": sha,
            "_importedAt": imported_at,
        }
        if row is None:
            row = SalesOrder(
                order_no=order_no,
                source_provider="jky_file",
                source_order_id=order_no,
                raw=raw,
            )
            db.add(row)
            created += 1
        else:
            if not row.source_provider or row.source_provider == "jky_file":
                row.source_provider = "jky_file"
                row.source_order_id = order_no
            row.raw = {**(row.raw or {}), **raw}
            updated += 1
        row.platform = str(_cell(payload, _CHANNEL_KEYS) or "").strip()
        row.order_type = str(_cell(payload, _TYPE_KEYS) or "").strip()
        row.order_status = str(_cell(payload, _STATUS_KEYS) or "").strip()
        row.pay_status = str(_cell(payload, _SETTLE_KEYS) or "").strip()
        amount = _num(_cell(payload, _AMOUNT_KEYS))
        paid = _num(_cell(payload, _PAID_KEYS))
        row.order_amount = amount if amount is not None else (paid if paid is not None else row.order_amount)
        row.paid_amount = paid if paid is not None else (amount if amount is not None else row.paid_amount)
        row.ordered_at = order_time
        row.paid_at = pay_at

    # 非成交订单保留为业务事实，只撤销财务投影；禁止物理删除或破坏其他同步通道身份。
    removed_cancelled = 0
    cancelled_preserved = 0
    if cancelled_nos:
        leftovers = {
            row.order_no: row
            for row in db.query(SalesOrder)
            .filter(SalesOrder.order_no.in_(list(cancelled_nos)))
            .all()
        }
        from app.services import finance_projection_service
        for order_no in sorted(cancelled_nos):
            payload = cancelled_payloads[order_no]
            row = leftovers.get(order_no)
            if row is None:
                row = SalesOrder(
                    order_no=order_no,
                    source_provider="jky_file",
                    source_order_id=order_no,
                    raw={},
                )
                db.add(row)
                db.flush()
            status = str(_cell(payload, _STATUS_KEYS) or "").strip()
            order_time = (
                _dt(_cell(payload, _ORDERTIME_KEYS))
                or _dt(_cell(payload, _PAYTIME_KEYS))
                or _dt(_cell(payload, _SHIPTIME_KEYS))
            )
            pay_at = _dt(_cell(payload, _PAYTIME_KEYS)) or order_time
            row.raw = {
                **(row.raw or {}),
                "orderStatus": status,
                "settleStatus": str(_cell(payload, _SETTLE_KEYS) or ""),
                "warehouse": str(_cell(payload, _WAREHOUSE_KEYS) or ""),
                "_source": "jky_sales_file",
                "_file": original_name,
                "_sha": sha,
                "_importedAt": imported_at,
            }
            row.order_status = status
            if not row.platform:
                row.platform = str(_cell(payload, _CHANNEL_KEYS) or "").strip()
            if not row.order_type:
                row.order_type = str(_cell(payload, _TYPE_KEYS) or "").strip()
            if not row.pay_status:
                row.pay_status = str(_cell(payload, _SETTLE_KEYS) or "").strip()
            if row.ordered_at is None:
                row.ordered_at = order_time
            if row.paid_at is None:
                row.paid_at = pay_at
            amount = _num(_cell(payload, _AMOUNT_KEYS))
            paid = _num(_cell(payload, _PAID_KEYS))
            if row.order_amount is None:
                row.order_amount = amount if amount is not None else paid
            if row.paid_amount is None:
                row.paid_amount = paid if paid is not None else amount
            finance_projection_service.delete_projected_source(
                db, "domestic_sales_order", str(row.id)
            )
            cancelled_preserved += 1

    db.flush()

    # ---------- sheet2 销售单货品：整单替换明细（item_groups 已在校验阶段解析） ----------
    item_total = 0
    item_order_hits = 0
    if item_ws is not None:
        sku_lookup = sales_sku_lookup(db)
        db_orders = {
            o.order_no: o
            for o in db.query(SalesOrder).filter(SalesOrder.order_no.in_(list(item_groups.keys()))).all()
        }
        for no, lines in item_groups.items():
            order = db_orders.get(no)
            if order is None:
                continue
            db.execute(delete(SalesOrderItem).where(SalesOrderItem.order_id == order.id))
            for line in lines:
                qty = _num(_cell(line, _ITEM_QTY_KEYS)) or 0
                unit = _num(_cell(line, _ITEM_PRICE_KEYS))
                amt = _num(_cell(line, _ITEM_AMOUNT_KEYS))
                discount = _num(_cell(line, _ITEM_DISCOUNT_KEYS))
                sku_code = str(_cell(line, _ITEM_SKU_KEYS) or "").strip()
                db.add(SalesOrderItem(
                    order_id=order.id,
                    sku_id=sku_lookup.get(sku_code.lower()),
                    sku_code=sku_code,
                    goods_name=str(_cell(line, _ITEM_NAME_KEYS) or "").strip(),
                    quantity=qty,
                    unit_price=unit,
                    amount=amt,
                    discount_amount=discount,
                    raw={
                        "spec": str(_cell(line, _ITEM_SPEC_KEYS) or ""),
                        "unitName": str(_cell(line, _ITEM_UNIT_KEYS) or ""),
                        "gift": str(_cell(line, _ITEM_GIFT_KEYS) or ""),
                        "_source": "jky_sales_file",
                    },
                ))
                item_total += 1
            item_order_hits += 1

    if item_total == 0:
        wb.close()
        db.rollback()
        raise ValueError("未读取到有效销售明细，订单主表未导入，请检查明细列和订单号是否对应")

    wb.close()

    # 销售业务一落库就同步财务事项，不再要求用户到月结页重复上传或手工回填。
    db.flush()
    from app.services import finance_projection_service
    projected_orders = 0
    for order in db_orders.values():
        if order.order_no in seen:
            finance_projection_service.project_domestic_sales_order(db, order)
            projected_orders += 1
    db.commit()

    dates = [
        r[0] for r in db.query(SalesOrder.ordered_at)
        .filter(SalesOrder.source_provider == "jky_file", SalesOrder.ordered_at.isnot(None))
        .all()
    ]
    stats = {
        "ok": True,
        "ordersInFile": order_idx + len(cancelled_nos),
        "ordersImported": len(seen),
        "created": created,
        "updated": updated,
        "cancelledSkipped": len(cancelled_nos),
        "removedCancelled": removed_cancelled,
        "cancelledPreserved": cancelled_preserved,
        "itemsImported": item_total,
        "itemOrderHits": item_order_hits,
        "financeProjectedOrders": projected_orders,
        "minDate": min(dates).strftime("%Y-%m-%d") if dates else None,
        "maxDate": max(dates).strftime("%Y-%m-%d") if dates else None,
        "file": original_name,
        "archiveFile": str(archive_path.relative_to(Path(settings.DATA_DIR).resolve())),
        "sheets": sheet_diagnostics,
    }
    audit(db, actor, "sales_file.import", "sales_orders", len(seen),
          {"ordersImported": len(seen), "cancelledSkipped": len(cancelled_nos),
           "removedCancelled": removed_cancelled, "items": item_total,
           "archiveFile": stats["archiveFile"], "sheets": sheet_diagnostics})
    return stats
