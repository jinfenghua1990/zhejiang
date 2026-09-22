from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

"""金额工具。全项目禁止 float 存钱，一律 Decimal / Numeric。"""

MONEY_QUANT = Decimal("0.0001")
CENT_QUANT = Decimal("0.01")


def to_decimal(value) -> Decimal:
    """安全转 Decimal；None → 0。禁止接收 float 参与计算（输入仅 str/int/Decimal）。"""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        raise TypeError("float 禁止参与金额计算，请传 str/int/Decimal")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"非法金额: {value!r}") from exc


def quantize(value: Decimal, quant: Decimal = MONEY_QUANT) -> Decimal:
    return value.quantize(quant, rounding=ROUND_HALF_UP)


def money_sum(values) -> Decimal:
    total = Decimal("0")
    for v in values:
        total += to_decimal(v)
    return quantize(total)


def is_zero(value: Decimal) -> bool:
    return quantize(value) == Decimal("0")


def money_str(value: Decimal) -> str:
    return f"{quantize(value, CENT_QUANT):,.2f}"
