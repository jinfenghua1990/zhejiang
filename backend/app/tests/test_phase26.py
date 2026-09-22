"""Phase 2/6 新增单测：金额平衡 / 分摊尾差 / Decimal 红线 / 成本与期初纯逻辑。"""
from decimal import Decimal

import pytest

from app.services.allocation import balance_check, distribute_expense
from app.utils.money import money_sum, to_decimal


# ---------- 金额红线（规格 2：禁止 float 存钱） ----------

def test_money_decimal_only():
    with pytest.raises(TypeError):
        to_decimal(12.34)  # float 禁止
    assert to_decimal("12.34") == Decimal("12.34")


def test_money_sum_decimal():
    assert money_sum([Decimal("1.1"), Decimal("2.2")]) == Decimal("3.3000")


# ---------- 采购金额平衡（规格 7.4） ----------

def test_balance_check_spec_example():
    """规格 7.4 示例：实付 50000 = 15000+15000+10000+5000+3000+2000。"""
    goods = [Decimal("15000"), Decimal("15000"), Decimal("10000")]
    expense = [Decimal("5000"), Decimal("3000"), Decimal("2000")]
    r = balance_check(Decimal("50000"), goods, expense)
    assert r["unallocated"] == 0
    assert r["balanced"] is True
    assert r["allow_mark_refined"] is True


def test_balance_check_unbalanced_rejects():
    r = balance_check(Decimal("50000"), [Decimal("30000")], [Decimal("10000")])
    assert r["unallocated"] == Decimal("10000")
    assert r["balanced"] is False
    assert r["allow_mark_refined"] is False


def test_expense_distribute_tail_diff_zero():
    """分摊尾差入最后一项，合计 = 总额。"""
    shares = distribute_expense(Decimal("100.01"), [Decimal("1"), Decimal("1"), Decimal("1")], "by_qty")
    assert money_sum(shares) == Decimal("100.01")
    assert len(shares) == 3


def test_balance_negative_paid():
    r = balance_check(Decimal("-10"), [Decimal("0")], [Decimal("0")])
    assert r["balanced"] is False
