"""期初初始化（规格 11）：一次性向导，允许不平，历史差异进差异池，不篡改历史订单。

- 系统启用日期
- 各平台期初待回款 / 浙江农信期初余额 / SKU 期初库存·成本 / 保证金 / 冻结款 / 其他
- 所有调整写审计日志
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.audit import audit
from app.models.catalog import ProductSku
from app.models.ops import OpeningAdjustment, OpeningBalance
from app.utils.money import to_decimal

KINDS = {"platform_receivable", "bank", "sku_inventory", "sku_cost", "deposit", "frozen", "other"}


def list_openings(db: Session) -> list[dict[str, Any]]:
    rows = db.query(OpeningBalance).order_by(OpeningBalance.kind, OpeningBalance.id).all()
    return [
        {
            "id": r.id, "kind": r.kind, "ref": r.ref,
            "amount": str(r.amount) if r.amount is not None else None,
            "quantity": str(r.quantity) if r.quantity is not None else None,
            "asOfDate": r.as_of_date.isoformat() if r.as_of_date else None,
            "note": r.note, "createdAt": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def upsert_opening(db: Session, *, opening_id: int | None = None, kind: str, ref: str = "",
                   amount: str | None = None, quantity: str | None = None,
                   as_of_date: date | None = None, note: str = "",
                   actor: str = "system") -> OpeningBalance:
    if kind not in KINDS:
        raise ValueError(f"非法期初类别: {kind}")
    amt = to_decimal(amount) if amount is not None else None
    qty = to_decimal(quantity) if quantity is not None else None
    if amt is None and qty is None:
        raise ValueError("金额与数量至少填写一项")

    if opening_id:
        row = db.get(OpeningBalance, opening_id)
        if not row:
            raise ValueError("期初记录不存在")
        old = (row.kind, row.ref, row.amount, row.quantity)
        row.kind, row.ref, row.amount, row.quantity = kind, ref, amt, qty
        row.as_of_date = as_of_date
        row.note = note
        db.commit()
        audit(db, actor, "opening.update", "opening_balances", row.id,
              {"from": {"kind": old[0], "amount": str(old[2]) if old[2] is not None else None},
               "to": {"kind": kind, "amount": str(amt) if amt is not None else None}})
        return row

    row = OpeningBalance(kind=kind, ref=ref, amount=amt, quantity=qty,
                         as_of_date=as_of_date, note=note)
    db.add(row)
    db.commit()
    audit(db, actor, "opening.create", "opening_balances", row.id,
          {"kind": kind, "ref": ref, "amount": str(amt) if amt is not None else None,
           "quantity": str(qty) if qty is not None else None})
    return row


def delete_opening(db: Session, opening_id: int, actor: str = "system") -> None:
    row = db.get(OpeningBalance, opening_id)
    if row:
        db.delete(row)
        db.commit()
        audit(db, actor, "opening.delete", "opening_balances", opening_id, {"kind": row.kind})


def adjust(db: Session, *, opening_id: int, delta: str, reason: str,
           actor: str = "system") -> OpeningAdjustment:
    """历史差异调整：单独保存、审计留痕，不篡改原记录与历史订单（规格 11）。"""
    row = db.get(OpeningBalance, opening_id)
    if not row:
        raise ValueError("期初记录不存在")
    d = to_decimal(delta)
    row.amount = (row.amount or Decimal("0")) + d
    adj = OpeningAdjustment(opening_id=opening_id, delta=d, reason=reason, created_by=actor)
    db.add(adj)
    db.commit()
    audit(db, actor, "opening.adjust", "opening_adjustments", adj.id,
          {"openingId": opening_id, "delta": str(d), "reason": reason})
    return adj


def opening_dashboard(db: Session) -> dict[str, Any]:
    """按类别汇总 + 差异池（adjustments 合计）。"""
    rows = db.query(OpeningBalance).all()
    by_kind: dict[str, Decimal] = {}
    for r in rows:
        by_kind[r.kind] = (by_kind.get(r.kind, Decimal("0")) + (r.amount or Decimal("0")))
    adjustments = db.query(OpeningAdjustment).all()
    diff_total = sum((a.delta or Decimal("0") for a in adjustments), Decimal("0"))
    sku_cost = db.query(ProductSku).filter(ProductSku.default_cost.isnot(None)).count()
    return {
        "byKind": {k: str(v) for k, v in sorted(by_kind.items())},
        "adjustmentCount": len(adjustments),
        "differencePool": str(diff_total),
        "skuWithCost": sku_cost,
    }
