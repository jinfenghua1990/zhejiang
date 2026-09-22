from decimal import Decimal

from app.services.allocation import allocated_totals, balance_check, distribute_expense


def test_balance_perfect_allocation():
    """规格 7.4 示例：实付 50000 = 3 SKU + 包装 + 加工 + 其他。"""
    result = balance_check(
        "50000",
        ["15000", "15000", "10000"],
        ["5000", "3000", "2000"],
    )
    assert result["balanced"] is True
    assert result["allow_mark_refined"] is True
    assert result["unallocated"] == Decimal("0.0000")


def test_balance_unallocated_blocks_refined():
    result = balance_check("50000", ["15000", "15000"], ["5000"])
    assert result["balanced"] is False
    assert result["allow_mark_refined"] is False
    assert result["unallocated"] == Decimal("15000.0000")


def test_totals():
    totals = allocated_totals(["100.1", "200.2"], ["50"])
    assert totals["goods"] == Decimal("300.3000")
    assert totals["expense"] == Decimal("50.0000")
    assert totals["total"] == Decimal("350.3000")


def test_distribute_by_qty_no_tail_loss():
    """分摊尾差必须归零：1 元分 3 份。"""
    shares = distribute_expense("1", [1, 1, 1], "by_qty")
    assert sum(shares) == Decimal("1.00")
    assert shares[0] == Decimal("0.33")


def test_distribute_empty_weights():
    assert distribute_expense("100", [], "by_qty") == []
