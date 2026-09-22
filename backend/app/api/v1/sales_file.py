"""销售清单：吉客云《销售单查询》Excel 导入 + 全部销售明细台账查询。"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.db import get_db
from app.models.sales import SalesOrder, SalesOrderItem
from app.services import sales_file_import_service as svc
from app.services.dashboard import sales_order_costs_partial, sales_order_item_costs
from app.utils.money import CENT_QUANT, quantize, to_decimal

router = APIRouter(prefix="/sales-file", tags=["sales-file"])


@router.post("/import")
async def import_sales_file(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    """上传吉客云客户端导出的《销售单查询.xlsx》（销售单+销售单货品 双 sheet）。

    按 JY 订单号幂等 upsert 到 sales_orders / sales_order_items，
    只落成交单（待发/已发/待确认收货/已完成），关闭/取消/作废/待审核/退货/退款单不导入；
    库内被本文件标记为不计入状态的残留单会删除。
    """
    try:
        content = await file.read()
        if len(content) > svc.MAX_FILE_BYTES:
            raise HTTPException(413, "文件超过 100 MiB 上限")
        return svc.import_sales_file(
            db,
            content=content,
            original_name=file.filename or "sales-list.xlsx",
            actor=current_actor(request),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/detail")
def sales_detail(
    q: str = Query("", description="订单号/网店单号/货品编号/货品名称 模糊"),
    platform: str = Query("", description="销售渠道精确筛选"),
    sku: str = Query("", description="货品编号精确筛选（总览 SKU 排行点击穿透用）"),
    status: str = Query("", description="订单状态精确筛选"),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    """全部销售明细：订单 × 货品行级台账。

    手工导入（jky_file）与吉客云 API 三通道（jky_web/jky_rpa/jky_api）写同一张
    sales_orders / sales_order_items，字段口径一致，这里不做来源区分、全量展示
    （含取消单，用订单状态列自行筛）。

    成本与毛利：销售数量、客户实付金额取销售单事实，货品成本按本系统采购入库
    加权平均成本计算，订单毛利 = 订单实付 − 订单成本，与业绩总览完全一致；
    订单任一明细缺 SKU / 数量 / 入库成本时成本与毛利均留空（前端显示「—」）。
    """
    cond = _detail_conditions(
        db, q=q, platform=platform, sku=sku, status=status,
        start_date=start_date, end_date=end_date,
    )
    base = _detail_base(db, cond)
    total = base.count()
    rows = (
        base.order_by(
            SalesOrder.ordered_at.desc().nullslast(),
            SalesOrder.order_no.desc(),
            SalesOrderItem.id,
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    platforms = [
        r[0] for r in db.query(SalesOrder.platform).filter(SalesOrder.platform != "").distinct().order_by(SalesOrder.platform).all()
    ]
    statuses = [
        r[0] for r in db.query(SalesOrder.order_status).filter(SalesOrder.order_status != "").distinct().order_by(SalesOrder.order_status).all()
    ]
    return {
        "rows": _serialize_detail_rows(db, rows, end_date),
        "total": total,
        "page": page,
        "pageSize": page_size,
        "platforms": platforms,
        "statuses": statuses,
    }


# 导出列：与「全部销售明细」页面的字段一一对应，方便导出后在 Excel 里核对。
DETAIL_EXPORT_COLUMNS: list[tuple[str, str]] = [
    ("orderNo", "订单号"),
    ("netOrderNo", "网店订单号"),
    ("orderedAt", "下单时间"),
    ("paidAt", "付款时间"),
    ("platform", "销售渠道"),
    ("orderType", "订单类型"),
    ("orderStatus", "订单状态"),
    ("payStatus", "付款状态"),
    ("settleStatus", "结算状态"),
    ("warehouse", "发货仓库"),
    ("logisticsCompany", "物流公司"),
    ("logisticsNo", "物流单号"),
    ("buyerNote", "买家留言"),
    ("skuCode", "货品编号"),
    ("goodsName", "货品名称"),
    ("spec", "规格"),
    ("unitName", "单位"),
    ("gift", "赠品"),
    ("quantity", "数量"),
    ("unitPrice", "单价"),
    ("amount", "行金额"),
    ("discountAmount", "优惠金额"),
    ("goodsCount", "订单货品数"),
    ("orderAmount", "订单金额"),
    ("paidAmount", "订单实付"),
    ("goodsCost", "订单成本"),
    ("grossProfit", "订单毛利"),
    ("lineCost", "子SKU成本"),
    ("linePaid", "子SKU收入（按成本比例分摊）"),
    ("lineGross", "子SKU毛利"),
    ("allocBasis", "分摊依据"),
    ("incompleteText", "成本提示"),
]

MAX_EXPORT_ROWS = 50_000

# 订单级汇总列：明细里订单字段逐行重复，直接求和会重复计算，这里按订单去重单独出一张
# 汇总表并带上合计公式，导出后不用再手工去重核算。
ORDER_EXPORT_COLUMNS: list[tuple[str, str]] = [
    ("orderNo", "订单号"),
    ("netOrderNo", "网店订单号"),
    ("orderedAt", "下单时间"),
    ("platform", "销售渠道"),
    ("orderType", "订单类型"),
    ("orderStatus", "订单状态"),
    ("payStatus", "付款状态"),
    ("warehouse", "发货仓库"),
    ("goodsCount", "订单货品数"),
    ("orderAmount", "订单金额"),
    ("paidAmount", "订单实付"),
    ("goodsCost", "订单成本"),
    ("grossProfit", "订单毛利"),
    ("incompleteText", "成本提示"),
]


def _detail_conditions(
    db: Session,
    *,
    q: str,
    platform: str,
    sku: str,
    status: str,
    start_date: date | None,
    end_date: date | None,
) -> list:
    """查询条件：列表与导出共用，保证导出与页面筛选结果完全一致。"""
    cond = []
    if q.strip():
        like = f"%{q.strip()}%"
        cond.append(or_(
            SalesOrder.order_no.ilike(like),
            SalesOrder.order_no.in_(
                db.query(SalesOrder.order_no).filter(SalesOrder.raw["netOrderNo"].astext.ilike(like))
            ),
            SalesOrderItem.sku_code.ilike(like),
            SalesOrderItem.goods_name.ilike(like),
        ))
    if platform:
        cond.append(SalesOrder.platform == platform)
    if sku.strip():
        cond.append(SalesOrderItem.sku_code == sku.strip())
    if status:
        cond.append(SalesOrder.order_status == status)
    if start_date:
        cond.append(SalesOrder.ordered_at >= start_date)
    if end_date:
        end_next = date.fromordinal(end_date.toordinal() + 1)
        cond.append(SalesOrder.ordered_at < end_next)
    return cond


def _detail_base(db: Session, cond: list):
    base = db.query(SalesOrderItem, SalesOrder).join(
        SalesOrder, SalesOrderItem.order_id == SalesOrder.id
    )
    if cond:
        base = base.filter(or_(*cond)) if len(cond) == 1 else base.filter(*cond)
    return base


ALLOC_BY_COST = "按行成本比例"
ALLOC_BY_AMOUNT = "按行金额比例"
ALLOC_NONE = "缺少可分摊依据"


def _split_order_amounts(
    rows: list[tuple[SalesOrderItem, SalesOrder]],
    line_costs: dict[int, Decimal | None],
    order_costs: dict[int, tuple[Decimal | None, bool]],
) -> dict[int, tuple[Decimal | None, Decimal | None, str]]:
    """把订单级的实付金额与成本拆到每一条子 SKU 行。

    - 行成本：数量 × 该 SKU 的入库加权单价，取整到分后把尾差补到最后一条有成本的行，
      保证同一订单 Σ 行成本 = 订单成本（已覆盖部分）；
    - 行收入：订单实付按行成本占比分摊（客户实付含赠品与优惠，用成本占比更贴近实际贡献）；
      整单有行缺成本时退回按行金额比例，并在「分摊依据」列说明；两者都取不到时不摊，
      避免把订单金额凭空摊到行上；尾差补到最后一行，保证 Σ 行收入 = 订单实付。
    """
    grouped: dict[int, list[SalesOrderItem]] = {}
    order_by_id: dict[int, SalesOrder] = {}
    for item, order in rows:
        grouped.setdefault(order.id, []).append(item)
        order_by_id[order.id] = order

    splits: dict[int, tuple[Decimal | None, Decimal | None, str]] = {}
    for order_id, items in grouped.items():
        order = order_by_id[order_id]
        order_cost = order_costs.get(order_id, (None, True))[0]

        # 行成本：以订单成本为锚，尾差补到最后一条有成本的行
        cost_items = [item for item in items if line_costs.get(item.id) is not None]
        line_cost_map: dict[int, Decimal | None] = {item.id: None for item in items}
        if cost_items and order_cost is not None:
            running = Decimal("0")
            last_cost_index = len(cost_items) - 1
            for index, item in enumerate(cost_items):
                if index == last_cost_index:
                    share = quantize(order_cost - running, CENT_QUANT)
                else:
                    share = quantize(line_costs[item.id], CENT_QUANT)
                    running += share
                line_cost_map[item.id] = share

        total = to_decimal(order.paid_amount) if order.paid_amount is not None else None
        if total is None:
            for item in items:
                splits[item.id] = (line_cost_map[item.id], None, ALLOC_NONE)
            continue

        basis_values: list[Decimal] | None = None
        basis_name = ""
        cost_basis = [line_costs.get(item.id) for item in items]
        if all(value is not None and value > 0 for value in cost_basis):
            basis_values = [value for value in cost_basis if value is not None]
            basis_name = ALLOC_BY_COST
        else:
            amount_basis = [
                to_decimal(item.amount) if item.amount is not None else None for item in items
            ]
            if all(value is not None and value > 0 for value in amount_basis):
                basis_values = [value for value in amount_basis if value is not None]
                basis_name = ALLOC_BY_AMOUNT
        if basis_values is None:
            for item in items:
                splits[item.id] = (line_cost_map[item.id], None, ALLOC_NONE)
            continue

        basis_total = sum(basis_values, Decimal("0"))
        allocated = Decimal("0")
        last_index = len(items) - 1
        for index, item in enumerate(items):
            if index == last_index:
                share = quantize(total - allocated, CENT_QUANT)
            else:
                share = quantize(total * basis_values[index] / basis_total, CENT_QUANT)
                allocated += share
            splits[item.id] = (line_cost_map[item.id], share, basis_name)
    return splits


def _serialize_detail_rows(db: Session, rows: list, end_date: date | None) -> list[dict]:
    """成本口径与业绩总览一致：吉客云销售单只提供销售数量与客户实付金额，
    货品成本一律按本系统采购入库明细的加权平均单价重算；导入文件自带的
    成本/毛利字段只作为 raw 原始留痕，不参与展示与计算。个别明细缺成本时
    按已覆盖部分出数并标记 costIncomplete，避免明细合计与业绩总览对不上。"""
    def _fmt(dt) -> str | None:
        return dt.strftime("%Y-%m-%d %H:%M") if dt else None

    costs_as_of = (end_date + timedelta(days=1)) if end_date else datetime.now(timezone.utc)
    orders = list({order.id: order for _item, order in rows}.values())
    order_costs = sales_order_costs_partial(db, orders, as_of=costs_as_of)
    # 子 SKU 拆解：行成本 = 销售数量 × 本系统入库加权单价；订单实付按行成本占比摊到每行，
    # 整单缺成本时退回按行金额占比，两者都取不到就不摊（留空 + 说明依据），
    # 分摊用「先按比例、尾差补到最后一行」的方式，保证 Σ 子 SKU 收入 = 订单实付。
    line_costs = sales_order_item_costs(db, [item for item, _order in rows], as_of=costs_as_of)
    splits = _split_order_amounts(rows, line_costs, order_costs)

    out_rows = []
    for item, order in rows:
        o_raw = order.raw or {}
        i_raw = item.raw or {}
        cost, cost_incomplete = order_costs.get(order.id, (None, True))
        paid = to_decimal(order.paid_amount) if order.paid_amount is not None else None
        gross = quantize(paid - cost, CENT_QUANT) if cost is not None and paid is not None else None
        line_cost, line_paid, alloc_basis = splits.get(item.id, (None, None, ""))
        line_gross = (
            quantize(line_paid - line_cost, CENT_QUANT)
            if line_paid is not None and line_cost is not None
            else None
        )
        out_rows.append({
            "itemId": item.id,
            "orderId": order.id,
            "orderNo": order.order_no,
            "netOrderNo": o_raw.get("netOrderNo") or "",
            "orderedAt": _fmt(order.ordered_at),
            "paidAt": _fmt(order.paid_at),
            "platform": order.platform or "",
            "orderType": order.order_type or "",
            "orderStatus": order.order_status or "",
            "payStatus": order.pay_status or "",
            "settleStatus": o_raw.get("settleStatus") or "",
            "warehouse": o_raw.get("warehouse") or "",
            "logisticsNo": o_raw.get("logisticsNo") or "",
            "logisticsCompany": o_raw.get("logisticsCompany") or "",
            "buyerNote": order.buyer_note or "",
            "goodsCount": o_raw.get("goodsCount"),
            "goodsCost": float(quantize(cost, CENT_QUANT)) if cost is not None else None,
            "costIncomplete": bool(cost_incomplete),
            "lineCost": float(line_cost) if line_cost is not None else None,
            "linePaid": float(line_paid) if line_paid is not None else None,
            "lineGross": float(line_gross) if line_gross is not None else None,
            "allocBasis": alloc_basis,
            "incompleteText": "缺采购入库成本，成本与毛利仅含已覆盖部分" if cost_incomplete else "",
            "grossProfit": float(gross) if gross is not None else None,
            "skuCode": item.sku_code or "",
            "goodsName": item.goods_name or "",
            "spec": i_raw.get("spec") or "",
            "unitName": i_raw.get("unitName") or "",
            "gift": i_raw.get("gift") or "",
            "quantity": float(item.quantity) if item.quantity is not None else None,
            "unitPrice": float(item.unit_price) if item.unit_price is not None else None,
            "amount": float(item.amount) if item.amount is not None else None,
            "discountAmount": float(item.discount_amount) if item.discount_amount is not None else None,
            "orderAmount": float(order.order_amount) if order.order_amount is not None else None,
            "paidAmount": float(order.paid_amount) if order.paid_amount is not None else None,
        })
    return out_rows


@router.get("/detail/export")
def sales_detail_export(
    q: str = Query(""),
    platform: str = Query(""),
    sku: str = Query(""),
    status: str = Query(""),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    db: Session = Depends(get_db),
) -> Response:
    """导出当前筛选条件下的全部销售明细 XLSX（与页面同一数据源与成本口径，不受分页限制）。

    成本/毛利列与「全部销售明细」页完全同一函数计算；缺入库成本的行在「成本提示」
    列标注，避免导出后误把已覆盖部分当成完整毛利。
    """
    from app.services.data_export_service import _workbook

    cond = _detail_conditions(
        db, q=q, platform=platform, sku=sku, status=status,
        start_date=start_date, end_date=end_date,
    )
    rows = (
        _detail_base(db, cond)
        .order_by(
            SalesOrder.ordered_at.desc().nullslast(),
            SalesOrder.order_no.desc(),
            SalesOrderItem.id,
        )
        .limit(MAX_EXPORT_ROWS)
        .all()
    )
    out_rows = _serialize_detail_rows(db, rows, end_date)
    headers = [label for _key, label in DETAIL_EXPORT_COLUMNS]
    export_rows = [
        {label: row.get(key) for key, label in DETAIL_EXPORT_COLUMNS}
        for row in out_rows
    ]

    order_rows: list[dict] = []
    seen_orders: set[str] = set()
    for row in out_rows:
        if row["orderNo"] in seen_orders:
            continue
        seen_orders.add(row["orderNo"])
        order_rows.append({label: row.get(key) for key, label in ORDER_EXPORT_COLUMNS})
    # 毛利列写成 Excel 公式（实付 − 成本，缺成本留空），合计行也走 SUM 公式，
    # 导出后改数字会自动重算，不需要再手工核一遍。
    for index, order_row in enumerate(order_rows, start=2):
        order_row["订单毛利"] = f'=IF(L{index}="","",K{index}-L{index})'
    total_row: dict = {"订单号": "合计"}
    if order_rows:
        last = len(order_rows) + 1
        total_row.update({
            "订单货品数": f"=SUM(I2:I{last})",
            "订单金额": f"=SUM(J2:J{last})",
            "订单实付": f"=SUM(K2:K{last})",
            "订单成本": f"=SUM(L2:L{last})",
            "订单毛利": f"=SUM(M2:M{last})",
        })
        if any(order_row.get("成本提示") for order_row in order_rows):
            total_row["成本提示"] = "本表含缺成本订单，成本与毛利按已覆盖部分计入"
    order_rows.append(total_row)

    content = _workbook([
        ("订单汇总", [label for _key, label in ORDER_EXPORT_COLUMNS], order_rows),
        ("销售明细", headers, export_rows),
    ])
    span = f"{start_date.isoformat()}_{end_date.isoformat()}" if start_date and end_date else date.today().isoformat()
    filename = f"销售明细-核验-{span}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "X-Export-Row-Count": str(len(export_rows)),
            "X-Export-Order-Count": str(len(seen_orders)),
            "Cache-Control": "no-store",
        },
    )
