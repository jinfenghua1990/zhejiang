from decimal import Decimal

import pytest

from app.utils.money import is_zero, money_str, money_sum, quantize, to_decimal


def test_to_decimal_from_str_and_int():
    assert to_decimal("50000") == Decimal("50000")
    assert to_decimal(15000) == Decimal("15000")
    assert to_decimal(None) == Decimal("0")


def test_float_is_rejected():
    with pytest.raises(TypeError):
        to_decimal(0.1)


def test_invalid_string_raises():
    with pytest.raises(ValueError):
        to_decimal("abc")


def test_money_sum_and_zero():
    assert money_sum(["15000", "15000", "10000", "5000", "3000", "2000"]) == Decimal("50000.0000")
    assert is_zero(Decimal("0.00001"))
    assert not is_zero(Decimal("0.01"))


def test_quantize_half_up():
    assert quantize(Decimal("1.005"), Decimal("0.01")) == Decimal("1.01")


def test_money_str():
    assert money_str(Decimal("50000")) == "50,000.00"
