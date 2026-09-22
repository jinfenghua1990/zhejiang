"""销售订单成交口径（2026-09-17 与用户确认，全系统共用）。

成交：待发（待发货-已递交）/ 已发（发货在途）/ 待确认收货 / 已完成 等正常流转状态，
     月度订单默认都落在这些状态里，一律计入。
不计入：关闭、取消、作废、待审核、退货、退款 —— 业绩总览、月结、近销、补货等
     所有聚合都按本口径过滤；明细台账仍全量展示，由订单状态列自行筛。

状态清单只在本模块维护，避免各模块各写一套导致口径漂移。
"""

from __future__ import annotations

from sqlalchemy import or_

from app.models.sales import SalesOrder

# 前缀匹配：吉客云会在状态后带后缀（如「已取消-被合并」「已取消-被拆分」）
NOT_DEAL_PREFIXES: tuple[str, ...] = (
    "已取消",
    "取消",
    "作废",
    "已关闭",
    "关闭",
    "退货",
    "退款",
    "待审核",
    "未审核",
    # 付款前订单不构成成交（成交从「待发货」起算）
    "待付款",
    "未付款",
    "未支付",
    "待支付",
)


def is_deal_status(status: str | None) -> bool:
    """单个订单状态是否计入成交；状态为空视为有效单（与导入通道原行为一致）。"""
    text = str(status or "").strip()
    if not text:
        return True
    return not text.startswith(NOT_DEAL_PREFIXES)


def deal_orders_condition():
    """SQLAlchemy 过滤条件：仅保留计入成交的销售订单。

    状态为空（未回填）的订单按有效单保留，与 is_deal_status 判定一致。
    """
    not_deal = or_(*[SalesOrder.order_status.like(f"{prefix}%") for prefix in NOT_DEAL_PREFIXES])
    return or_(SalesOrder.order_status.is_(None), ~not_deal)