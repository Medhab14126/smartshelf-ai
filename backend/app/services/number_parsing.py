"""Locale-tolerant parsing of quantities and money amounts from OCR text."""

import re
from decimal import Decimal, InvalidOperation

_CURRENCY_NOISE = re.compile(r"(₹|rs\.?|inr|usd|eur|gbp|\$|€|£|/-)", re.IGNORECASE)
NUMBER_TOKEN = re.compile(r"(?<![\w.,])[-+]?\d[\d,.\s]*\d(?![\w])|(?<![\w.,])\d(?![\w.,])")


def parse_amount(text: str | None) -> Decimal | None:
    """Parse '1,234.50', '1.234,50', '1,23,456.00', '₹600/-', '600' -> Decimal.

    Returns None when the text is not a single unambiguous number.
    """
    if text is None:
        return None
    cleaned = _CURRENCY_NOISE.sub("", str(text)).strip().replace(" ", "")
    if not cleaned or not re.fullmatch(r"[-+]?[\d.,]+", cleaned):
        return None
    sign = -1 if cleaned.startswith("-") else 1
    cleaned = cleaned.lstrip("+-")
    last_dot, last_comma = cleaned.rfind("."), cleaned.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        # Whichever separator comes last is the decimal separator.
        if last_dot > last_comma:
            cleaned = cleaned.replace(",", "")
        else:
            cleaned = cleaned.replace(".", "").replace(",", ".")
    elif last_comma >= 0:
        # "12,50" -> decimal comma; "1,250" / "1,23,456" -> thousands.
        decimals = len(cleaned) - last_comma - 1
        cleaned = (
            cleaned.replace(",", ".") if decimals in (1, 2) and cleaned.count(",") == 1 else cleaned.replace(",", "")
        )
    elif cleaned.count(".") > 1:
        # "1.234.567" thousands separators
        cleaned = cleaned.replace(".", "")
    try:
        value = Decimal(cleaned) * sign
    except InvalidOperation:
        return None
    return value


def approx_equal(a: Decimal | None, b: Decimal | None, tolerance: Decimal = Decimal("0.02")) -> bool:
    """Compare money values allowing rounding differences (relative 0.5% or abs tolerance)."""
    if a is None or b is None:
        return False
    diff = abs(a - b)
    return diff <= tolerance or diff <= abs(b) * Decimal("0.005")
