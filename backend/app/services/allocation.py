"""采购金额分配与平衡校验（规格 7.4）。纯函数，便于单元测试。"""

from decimal import Decimal

from app.utils.money import is_zero, money_sum, to_decimal


def allocated_totals(goods_amounts: list, expense_amounts: list) -> dict[str, Decimal]:
    """返回商品已分配 / 费用已分配 / 合计。"""
    goods = money_sum(goods_amounts)
    expense = money_sum(expense_amounts)
    return {"goods": goods, "expense": expense, "total": goods + expense}


def balance_check(paid_amount, goods_amounts: list, expense_amounts: list) -> dict:
    """未分配 = 实付 − 已分配。未分配 != 0 时禁止标记“采购内容完整”。"""
    paid = to_decimal(paid_amount)
    totals = allocated_totals(goods_amounts, expense_amounts)
    unallocated = paid - totals["total"]
    return {
        "paid": paid,
        "goods_allocated": totals["goods"],
        "expense_allocated": totals["expense"],
        "unallocated": unallocated,
        "balanced": is_zero(unallocated),
        "allow_mark_refined": is_zero(unallocated),
    }


def distribute_expense(amount, weights: list, method: str) -> list[Decimal]:
    """附加费用分摊：按数量 / 按商品金额 / 手工比例。尾差入最后一项，保证合计=总额。"""
    total = to_decimal(amount)
    if not weights or total == 0:
        return []
    ws = [to_decimal(w) for w in weights]
    wsum = sum(ws)
    if wsum == 0:
        return []
    # 手工模式 w 即比例；其余模式 w 为数量/金额权重。权重和不为 1 时
    # 做归一化（除以权重和），避免尾差全部落入最后一项导致失真。
    shares = [total * w / wsum for w in ws]
    shares = [s.quantize(Decimal("0.01")) for s in shares]
    diff = total - sum(shares)
    if shares:
        shares[-1] += diff
    return shares
