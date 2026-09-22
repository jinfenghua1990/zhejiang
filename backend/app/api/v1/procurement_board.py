"""采购工作台（截图版）API：单页呈现 1688 → 采购 → 入库 → 发票 全流程看板。

与 /procurement-workbench（5 步骤执行视角）并存独立入口，
复用 procurement_chain_service 与 procurement_board_service 的底层数据。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import procurement_board_service as service

router = APIRouter(prefix="/procurement-board", tags=["采购工作台"])


@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> dict:
    """顶部 5 数字 + 1 付款率进度环。"""
    return service.overview(db)


@router.get("/orders")
def orders(
    status: str = Query("all", pattern="^(all|pending|refine|po|inbound|invoice|done)$"),
    q: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """左侧订单列表：时间分组 + 5 进度圆点 + 状态徽章。"""
    return service.list_board_orders(db, status=status, q=q, page=page, page_size=page_size)


@router.get("/orders/{order_id}/detail")
def order_detail(order_id: int, db: Session = Depends(get_db)) -> dict:
    """右侧订单详情（5 卡：基本信息 / 流程状态 / 供应商档案 / 常购 SKU / 金额与付款分层）。"""
    data = service.order_detail(db, order_id)
    if data is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    return data