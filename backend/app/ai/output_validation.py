"""Validate and ground LLM output.

`parse_structured` validates JSON against a Pydantic model. Grounding checks
then verify that values actually appear in the source text; values that do
not are removed (header fields) or flagged (line items) so the model cannot
introduce information that is not in the document.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.services.number_parsing import NUMBER_TOKEN, parse_amount

M = TypeVar("M", bound=BaseModel)


class OutputValidationError(ValueError):
    pass


def parse_structured(raw: str, model: type[M]) -> M:
    raw = (raw or "").strip()
    # Some models wrap JSON in code fences despite the schema constraint.
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", raw, re.S)
    if fenced:
        raw = fenced.group(1)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OutputValidationError(f"response is not valid JSON ({exc.msg})") from exc
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        errors = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5])
        raise OutputValidationError(errors) from exc


def normalise_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def text_contains(source_norm: str, value: str | None) -> bool:
    if not value:
        return False
    needle = normalise_text(value)
    if not needle:
        return False
    return needle in source_norm or needle.replace(" ", "") in source_norm.replace(" ", "")


def token_coverage(source_norm: str, value: str) -> float:
    """Share of the value's words (3+ chars or digits) found in the source."""
    tokens = [t for t in normalise_text(value).split() if len(t) >= 3 or t.isdigit()]
    if not tokens:
        return 0.0
    source_tokens = set(source_norm.split())
    joined = source_norm.replace(" ", "")
    found = sum(1 for t in tokens if t in source_tokens or t in joined)
    return found / len(tokens)


def numbers_in_text(text: str) -> set[Decimal]:
    values: set[Decimal] = set()
    for match in NUMBER_TOKEN.finditer(text or ""):
        token = match.group(0)
        # NUMBER_TOKEN allows spaces inside a number ("1 665,00"), so table columns such as "10  45.00  450.00"
        # come back as one token. Keep the whole reading and each separately printed piece.
        for candidate in (token, *re.split(r",?\s+", token)):
            value = parse_amount(candidate)
            if value is not None:
                values.add(value.normalize())
    return values


def number_grounded(value: Decimal | None, numbers: set[Decimal]) -> bool:
    if value is None:
        return True
    return value.normalize() in numbers
