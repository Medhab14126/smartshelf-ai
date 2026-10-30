"""Canonical unit names shared with the frontend unit picker (`frontend/src/lib/units.ts`).

Invoices and labels spell units many ways ("Ltr", "litres", "gms"); one spelling per unit keeps a
product's stock in a single unit and matches the picker options. Unknown units are kept as written.
"""

from __future__ import annotations

import re
from decimal import Decimal

_ALIASES = {
    "pcs": ("pcs", "pc", "piece", "pieces", "nos", "no", "unit", "units", "ea", "each"),
    "g": ("g", "gm", "gms", "gram", "grams", "gr", "grm"),
    "kg": ("kg", "kgs", "kilo", "kilos", "kilogram", "kilograms"),
    "ml": ("ml", "mls", "millilitre", "millilitres", "milliliter", "milliliters"),
    "ltr": ("ltr", "ltrs", "l", "lt", "litre", "litres", "liter", "liters"),
    "packet": ("packet", "packets", "pkt", "pkts", "pack", "packs"),
    "bottle": ("bottle", "bottles", "btl"),
    "box": ("box", "boxes"),
    "dozen": ("dozen", "dz"),
}
_CANONICAL = {alias: name for name, aliases in _ALIASES.items() for alias in aliases}


def canonical_unit(unit: str | None) -> str | None:
    if unit is None or not unit.strip():
        return None
    cleaned = unit.strip()
    return _CANONICAL.get(cleaned.lower().rstrip("."), cleaned)


# Units a pack size can be measured in (the picker offers only these; counts are the quantity itself).
PACK_UNITS = ("g", "kg", "ml", "ltr")
_PACK_SIZE = re.compile(
    r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*(ml|mls|l|lt|ltr|ltrs|litres?|liters?|g|gm|gms|gr|grams?|kg|kgs|kilos?)\b",
    re.IGNORECASE,
)


def parse_pack_size(text: str | None) -> tuple[Decimal, str] | None:
    """Read a pack size such as "500ml", "1 L" or "200 g" printed in a product name or label.

    The last match wins, so "Milk 500ml" and "Flour (25 kg)" both work. Only text actually present
    is used; nothing is inferred.
    """
    matches = list(_PACK_SIZE.finditer(text or ""))
    if not matches:
        return None
    value, unit = matches[-1].groups()
    size = Decimal(value.replace(",", "."))
    if size <= 0:
        return None
    return size, canonical_unit(unit) or unit


def pack_from_line(product_name: str | None, unit: str | None) -> tuple[Decimal, str] | None:
    """Pack size of an invoice line: printed in the name ("Milk 500ml"), else a measure unit means 1 of it.

    "Sugar, 5 kg @ 45/kg" has no size in the name; its quantity is then 5 packs of 1 kg, which keeps the
    stock count and the per-unit price meaningful.
    """
    found = parse_pack_size(product_name)
    if found:
        return found
    canonical = canonical_unit(unit)
    return (Decimal(1), canonical) if canonical in PACK_UNITS else None
