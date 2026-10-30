"""Invoice text -> structured invoice data.

Pipeline (see `extract_invoice`):

1. Rule-based parser: deterministic, layout-tolerant line parsing. Always runs
   and is the fallback when the LLM is unavailable.
2. Local LLM (Ollama) with a JSON schema, validated by Pydantic, retried a
   bounded number of times with the validation error as feedback.
3. Grounding: LLM values that do not appear in the OCR text are discarded or
   flagged, so nothing is invented.
4. Deterministic validation: arithmetic checks, totals, dates, duplicates.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from itertools import combinations

from app.ai.llm_client import LLMUnavailableError, get_llm_client
from app.ai.output_validation import (
    OutputValidationError,
    normalise_text,
    number_grounded,
    numbers_in_text,
    parse_structured,
    text_contains,
    token_coverage,
)
from app.ai.prompts import INVOICE_RETRY_SUFFIX, INVOICE_SYSTEM, INVOICE_USER
from app.core.config import get_settings
from app.schemas.extraction import ExtractedInvoiceItem, InvoiceExtraction
from app.services.date_extraction_service import ParseStatus, parse_date_string
from app.services.number_parsing import approx_equal, parse_amount

logger = logging.getLogger(__name__)

UNITS = {
    "pcs", "pc", "piece", "pieces", "nos", "no", "unit", "units", "ea", "each", "kg", "kgs", "g", "gm", "gms",
    "gram", "grams", "l", "lt", "ltr", "ltrs", "litre", "litres", "liter", "liters", "ml", "packet", "packets",
    "pkt", "pkts", "pack", "packs", "box", "boxes", "case", "cases", "ctn", "carton", "cartons", "dozen", "dz",
    "bottle", "bottles", "btl", "can", "cans", "bag", "bags", "tray", "trays", "jar", "jars", "tin", "tins",
    "loaf", "loaves", "tub", "tubs", "roll", "rolls", "sachet", "sachets", "crate", "crates",
}  # fmt: skip

_SKIP_LINE = re.compile(
    r"\b(sub\s*-?\s*total|total|tax|gst|cgst|sgst|igst|vat|cess|discount|round(?:ing)?\s*off|balance|amount\s+due|"
    r"amount\s+in\s+words|invoice|bill\s+(?:no|date)|date|phone|tel|mobile|email|e-mail|gstin|pan|fssai|address|"
    r"bank|a/c|account|ifsc|thank|page|signature|terms|due|payment|shipping|delivery\s+charge|freight|"
    r"bill\s+to|ship\s+to|sold\s+to|customer)\b",
    re.I,
)
_HEADER_WORDS = re.compile(
    r"\b(description|item|product|particulars|qty|quantity|rate|price|amount|unit|uom|hsn|sku|code)\b", re.I
)
_CODE_TOKEN = re.compile(r"^(?=.*\d)[A-Z0-9]{2,}(?:[-/][A-Z0-9]+)*$")
_SERIAL = re.compile(r"^\(?\d{1,3}[.)]?$")
_QTY_UNIT = re.compile(r"^(\d+(?:[.,]\d+)?)([a-zA-Z]{1,8})$")
_CURRENCY_TOKENS = {"₹", "rs", "rs.", "inr", "$", "usd", "€", "eur", "£", "gbp", "/-"}


@dataclass
class ExtractionOutcome:
    extraction: InvoiceExtraction
    method: str
    warnings: list[str] = field(default_factory=list)
    item_warnings: list[list[str]] = field(default_factory=list)
    item_confidence: list[float] = field(default_factory=list)
    confidence: float = 0.0
    llm_error: str | None = None
    invoice_date_value: object = None  # datetime.date | None


# ---------------------------------------------------------------------------
# Rule-based parsing
# ---------------------------------------------------------------------------


def _detect_currency(text: str) -> str | None:
    lowered = text.lower()
    explicit = re.search(r"currency\s*[:\-]?\s*([A-Za-z]{3})\b", text, re.I)
    if explicit:
        return explicit.group(1).upper()
    checks = [
        ("INR", r"₹|\binr\b|\brs\.?\s*\d|\brs\b"),
        ("USD", r"\$|\busd\b"),
        ("EUR", r"€|\beur\b"),
        ("GBP", r"£|\bgbp\b"),
        ("AED", r"\baed\b"),
        ("SGD", r"\bsgd\b"),
    ]
    found = [code for code, pattern in checks if re.search(pattern, lowered)]
    return found[0] if len(found) == 1 else None


def _last_amount(line: str) -> Decimal | None:
    tokens = [t for t in re.split(r"\s+", line) if t and "%" not in t]
    for token in reversed(tokens):
        value = parse_amount(token.strip(":"))
        if value is not None:
            return value
    return None


def _parse_header(lines: list[str], text: str) -> dict:
    header: dict = {
        "supplier_name": None,
        "invoice_number": None,
        "invoice_date": None,
        "subtotal": None,
        "tax": None,
        "total": None,
    }

    for line in lines:
        if header["invoice_number"] is None:
            m = re.search(
                r"\b(?:invoice|inv|bill|receipt)\s*(?:no\.?|number|num|#|id)?\s*[:#.\-]?\s*(?:no\.?\s*[:#]?\s*)?([A-Za-z0-9][A-Za-z0-9\-/_.]{1,30})",
                line,
                re.I,
            )
            if m and re.search(r"\d", m.group(1)) and not re.match(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$", m.group(1)):
                header["invoice_number"] = m.group(1).rstrip(".")
        if header["invoice_date"] is None:
            m = re.search(
                r"(?<!due\s)\b(?:invoice\s*date|bill\s*date|date\s*of\s*issue|issue\s*date|dated|date)\b\s*[:\-]?\s*(.+)",
                line,
                re.I,
            )
            if m and not re.search(r"\b(due|delivery|expiry|order)\s*date", line, re.I):
                parsed = parse_date_string(m.group(1))
                if parsed and parsed.status != ParseStatus.INVALID:
                    header["invoice_date"] = parsed.raw

    total_candidates: list[tuple[int, Decimal]] = []
    tax_total: Decimal | None = None
    tax_parts: list[Decimal] = []
    for line in lines:
        low = line.lower()
        amount = _last_amount(line)
        if amount is None:
            continue
        if re.search(r"sub\s*-?\s*total|taxable\s*(?:value|amount)|total\s*before\s*tax", low):
            header["subtotal"] = amount
        elif re.search(r"\btotal\s*(?:tax|gst|vat)\b|\btax\s*total\b", low):
            tax_total = amount
        elif re.search(r"\b(c?gst|sgst|igst|utgst|vat|tax|cess)\b", low) and not re.search(r"\btotal\b|invoice", low):
            tax_parts.append(amount)
        elif re.search(
            r"grand\s*total|total\s*amount|invoice\s*total|net\s*(?:amount|total|payable)|amount\s*payable|total\s*due|balance\s*due|amount\s*due",
            low,
        ):
            total_candidates.append((2, amount))
        elif re.search(r"\btotal\b", low):
            total_candidates.append((1, amount))
    header["tax"] = tax_total if tax_total is not None else (sum(tax_parts, Decimal(0)) if tax_parts else None)
    if total_candidates:
        best_rank = max(rank for rank, _ in total_candidates)
        header["total"] = [amount for rank, amount in total_candidates if rank == best_rank][-1]

    # Supplier: explicit label first, else the first prominent header line.
    for line in lines[:25]:
        m = re.match(r"^\s*(?:supplier|vendor|sold\s*by|seller|from|billed\s*by)\s*[:\-]\s*(.+)$", line, re.I)
        if m and re.search(r"[A-Za-z]{3}", m.group(1)):
            header["supplier_name"] = m.group(1).strip()
            break
    if header["supplier_name"] is None:
        header["supplier_name"] = _guess_supplier(lines)
    return header


def _guess_supplier(lines: list[str]) -> str | None:
    """First prominent business-name-like text above the item table."""
    for line in lines[:8]:
        if len(set(m.lower() for m in _HEADER_WORDS.findall(line))) >= 2:
            break  # reached the item table header
        for segment in re.split(r"\s{2,}", line.strip()):
            # "CafeWare Packaging Co Invoice No: 123" -> text before the keyword
            segment = re.split(r"\b(?:tax\s*invoice|invoice|bill|receipt|cash\s*memo)\b", segment, flags=re.I)[0]
            segment = segment.strip(" -:|#")
            if not re.search(r"[A-Za-z]{3}", segment) or _SKIP_LINE.search(segment):
                continue
            if sum(c.isdigit() for c in segment) > len(segment) / 3:
                continue
            return segment[:200]
    return None


def _parse_item_line(line: str) -> ExtractedInvoiceItem | None:
    tokens = [t for t in re.split(r"\s+", line.strip()) if t]
    if len(tokens) < 2:
        return None

    # Walk from the right collecting numbers, units and currency markers.
    numbers: list[Decimal] = []
    positions: list[int] = []  # token index of each collected number
    unit: str | None = None
    index = len(tokens)
    while index > 0:
        token = tokens[index - 1]
        low = token.lower().strip(":")
        if low in _CURRENCY_TOKENS or "%" in token:
            index -= 1
            continue
        # "3kg" is a quantity+unit only in the quantity slot (at most price and
        # total to its right); further left it is a package size ("Milk 500ml").
        qty_unit = _QTY_UNIT.match(token)
        if qty_unit and qty_unit.group(2).lower() in UNITS:
            if unit is not None or len(numbers) > 2:
                break
            numbers.insert(0, parse_amount(qty_unit.group(1)) or Decimal(0))
            positions.insert(0, -1)  # never returned to the description
            unit = qty_unit.group(2)
            index -= 1
            continue
        # A unit word must follow its quantity ("20 packet"), else it is part of the name.
        if low in UNITS and numbers and unit is None and index >= 2 and parse_amount(tokens[index - 2]) is not None:
            unit = token
            index -= 1
            continue
        value = parse_amount(token.lstrip("₹$€£").replace("/-", ""))
        if value is not None and not re.search(r"[A-Za-z]", token):
            numbers.insert(0, value)
            positions.insert(0, index - 1)
            index -= 1
            continue
        break

    description_tokens = tokens[:index]
    if description_tokens and _SERIAL.match(description_tokens[0]) and len(description_tokens) > 1:
        description_tokens = description_tokens[1:]
    product_code = None
    if len(description_tokens) > 1 and _CODE_TOKEN.match(description_tokens[0]):
        product_code = description_tokens.pop(0)
    if len(re.findall(r"[A-Za-z]", " ".join(description_tokens))) < 2 or not numbers:
        return None

    quantity = unit_price = line_total = None
    first_used = 0  # index in `numbers` of the first value used as a column
    if len(numbers) >= 3:
        # Prefer a (qty, price, total) triple whose arithmetic agrees.
        best = None
        for i, j, k in combinations(range(len(numbers)), 3):
            q, p, t = numbers[i], numbers[j], numbers[k]
            if q > 0 and approx_equal((q * p).quantize(Decimal("0.01"), ROUND_HALF_UP), t):
                score = (k == len(numbers) - 1, -(k - i))
                if best is None or score > best[0]:
                    best = (score, i, q, p, t)
        if best:
            _, first_used, quantity, unit_price, line_total = best
        else:
            first_used = len(numbers) - 3
            quantity, unit_price, line_total = numbers[-3], numbers[-2], numbers[-1]
    elif len(numbers) == 2:
        first, second = numbers
        if first == first.to_integral_value() and first <= 10_000:
            quantity, line_total = first, second
        else:
            unit_price, line_total = first, second
    else:
        line_total = numbers[0]

    # Unused short numbers directly after the name belong to it ("Eggs Tray 30");
    # long integers there are HSN/SAC tax codes and are dropped.
    for pos in positions[:first_used]:
        token = tokens[pos] if pos >= 0 else ""
        if pos >= 0 and pos == index and len(token) <= 3:
            description_tokens.append(token)
            index += 1
    description = " ".join(description_tokens).strip(" -:|")

    try:
        return ExtractedInvoiceItem(
            product_name=description[:300],
            product_code=product_code,
            quantity=quantity,
            unit=unit,
            unit_price=unit_price,
            line_total=line_total,
        )
    except ValueError:
        return None


def parse_invoice_rules(text: str) -> InvoiceExtraction:
    lines = [ln.rstrip() for ln in (text or "").splitlines() if ln.strip()]
    header = _parse_header(lines, text)

    # Locate the item table: after a header row, before the totals block.
    start = 0
    for i, line in enumerate(lines):
        if len(set(m.lower() for m in _HEADER_WORDS.findall(line))) >= 2 and not re.search(r"\d+[.,]\d{2}", line):
            start = i + 1
            break
    items: list[ExtractedInvoiceItem] = []
    for line in lines[start:]:
        if re.search(r"\b(sub\s*-?\s*total|grand\s*total|total\s*amount|total)\b", line, re.I) and items:
            break
        if _SKIP_LINE.search(line):
            continue
        item = _parse_item_line(line)
        if item is None:
            continue
        # Outside a detected table, single-number lines are too unreliable.
        if start == 0 and item.quantity is None and item.unit_price is None:
            continue
        items.append(item)

    return InvoiceExtraction(
        supplier_name=header["supplier_name"],
        invoice_number=header["invoice_number"],
        invoice_date=header["invoice_date"],
        currency=_detect_currency(text),
        subtotal=header["subtotal"],
        tax=header["tax"],
        total=header["total"],
        items=items,
    )


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------


def _llm_schema() -> dict:
    """JSON schema sent to Ollama (numbers as numbers, everything nullable)."""
    nullable_str = {"type": ["string", "null"]}
    nullable_num = {"type": ["number", "null"]}
    return {
        "type": "object",
        "properties": {
            "supplier_name": nullable_str,
            "invoice_number": nullable_str,
            "invoice_date": nullable_str,
            "currency": nullable_str,
            "subtotal": nullable_num,
            "tax": nullable_num,
            "total": nullable_num,
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "product_name": {"type": "string"},
                        "product_code": nullable_str,
                        "quantity": nullable_num,
                        "unit": nullable_str,
                        "unit_price": nullable_num,
                        "line_total": nullable_num,
                    },
                    "required": ["product_name", "quantity", "unit", "unit_price", "line_total"],
                },
            },
        },
        "required": [
            "supplier_name",
            "invoice_number",
            "invoice_date",
            "currency",
            "subtotal",
            "tax",
            "total",
            "items",
        ],
    }


def extract_with_llm(text: str) -> InvoiceExtraction:
    settings = get_settings()
    client = get_llm_client()
    # Bound the prompt size; very long OCR output is truncated, not dropped silently.
    document = text[:12000]
    prompt = INVOICE_USER.format(text=document)
    last_error: str | None = None
    for _attempt in range(max(1, settings.llm_max_attempts)):
        user = prompt + (INVOICE_RETRY_SUFFIX.format(error=last_error) if last_error else "")
        raw = client.chat_json(model=settings.ollama_model, system=INVOICE_SYSTEM, user=user, schema=_llm_schema())
        try:
            return parse_structured(raw, InvoiceExtraction)
        except OutputValidationError as exc:
            last_error = str(exc)
            logger.warning("LLM invoice output rejected: %s", last_error)
    raise OutputValidationError(
        f"model output failed validation after {settings.llm_max_attempts} attempts: {last_error}"
    )


def ground_extraction(extraction: InvoiceExtraction, text: str) -> tuple[InvoiceExtraction, list[str], list[list[str]]]:
    """Remove/flag LLM values that do not appear in the document text."""
    source = normalise_text(text)
    numbers = numbers_in_text(text)
    warnings: list[str] = []
    data = extraction.model_copy(deep=True)

    for field_name in ("invoice_number", "supplier_name"):
        value = getattr(data, field_name)
        if value and not (text_contains(source, value) or token_coverage(source, value) >= 0.6):
            warnings.append(f"Discarded {field_name.replace('_', ' ')} '{value}': not found in the document text")
            setattr(data, field_name, None)
    if data.invoice_date and not text_contains(source, data.invoice_date):
        warnings.append(f"Discarded invoice date '{data.invoice_date}': not found in the document text")
        data.invoice_date = None
    for field_name in ("subtotal", "tax", "total"):
        value = getattr(data, field_name)
        if value is not None and not number_grounded(value, numbers):
            warnings.append(f"Discarded {field_name} {value}: not found in the document text")
            setattr(data, field_name, None)

    kept: list[ExtractedInvoiceItem] = []
    item_warnings: list[list[str]] = []
    for item in data.items:
        coverage = token_coverage(source, item.product_name)
        if coverage < 0.5:
            warnings.append(f"Ignored line '{item.product_name}': product name not found in the document text")
            continue
        notes: list[str] = []
        for field_name in ("quantity", "unit_price", "line_total"):
            value = getattr(item, field_name)
            if value is not None and not number_grounded(value, numbers):
                notes.append(f"{field_name.replace('_', ' ')} {value} was not found in the document; please verify")
        if coverage < 1:
            notes.append("product name only partly matches the document text")
        kept.append(item)
        item_warnings.append(notes)
    data.items = kept
    return data, warnings, item_warnings


# ---------------------------------------------------------------------------
# Deterministic validation
# ---------------------------------------------------------------------------


def validate_extraction(
    extraction: InvoiceExtraction, date_preference: str
) -> tuple[list[str], list[list[str]], list[float], object]:
    warnings: list[str] = []
    item_warnings: list[list[str]] = []
    confidences: list[float] = []

    for index, item in enumerate(extraction.items):
        notes: list[str] = []
        score = 1.0
        if item.quantity is None:
            notes.append("quantity missing")
            score -= 0.3
        elif item.quantity == 0:
            notes.append("quantity is zero")
            score -= 0.3
        if item.unit_price is None:
            notes.append("unit price missing")
            score -= 0.1
        if item.line_total is None:
            notes.append("line total missing")
            score -= 0.1
        if item.quantity is not None and item.unit_price is not None and item.line_total is not None:
            expected = (item.quantity * item.unit_price).quantize(Decimal("0.01"), ROUND_HALF_UP)
            if not approx_equal(expected, item.line_total):
                notes.append(f"quantity × unit price = {expected}, but line total is {item.line_total}")
                score -= 0.4
        # No note for a missing unit: many invoices only give counts, which are received as pcs.
        if not item.unit:
            score -= 0.05
        for other_index, other in enumerate(extraction.items[:index]):
            if _similar(item.product_name, other.product_name):
                notes.append(
                    f"very similar to line {other_index + 1} ('{other.product_name}'); check it is not a duplicate"
                )
                score -= 0.1
                break
        item_warnings.append(notes)
        confidences.append(max(0.05, round(score, 2)))

    line_sum = sum((i.line_total for i in extraction.items if i.line_total is not None), Decimal(0))
    if not extraction.items:
        warnings.append("No line items were detected. Add the items manually.")
    elif any(i.line_total is None for i in extraction.items):
        pass
    elif extraction.subtotal is not None and not approx_equal(line_sum, extraction.subtotal):
        warnings.append(f"Line totals add up to {line_sum}, but the subtotal is {extraction.subtotal}")
    elif (
        extraction.subtotal is None
        and extraction.tax is None
        and extraction.total is not None
        and not approx_equal(line_sum, extraction.total)
    ):
        warnings.append(f"Line totals add up to {line_sum}, but the invoice total is {extraction.total}")
    if extraction.subtotal is not None and extraction.total is not None:
        expected_total = extraction.subtotal + (extraction.tax or Decimal(0))
        if not approx_equal(expected_total, extraction.total):
            warnings.append(
                f"Subtotal + tax = {expected_total}, but the total is {extraction.total} (discounts or charges may be missing)"
            )
    for name in ("supplier_name", "invoice_number", "total"):
        if getattr(extraction, name) is None:
            warnings.append(f"{name.replace('_', ' ').capitalize()} not found")
    if extraction.currency is None:
        warnings.append("Currency not shown on the invoice; please confirm it")

    invoice_date = None
    if extraction.invoice_date:
        parsed = parse_date_string(extraction.invoice_date, date_preference)
        if parsed is None or parsed.status == ParseStatus.INVALID:
            warnings.append(f"Invoice date '{extraction.invoice_date}' is not a valid date")
        else:
            invoice_date = parsed.value
            if parsed.status == ParseStatus.AMBIGUOUS:
                alternatives = ", ".join(d.isoformat() for d in parsed.alternatives)
                warnings.append(
                    f"Invoice date '{parsed.raw}' is ambiguous; read as {parsed.value.isoformat()} (could be {alternatives})"
                )
    else:
        warnings.append("Invoice date not found")
    return warnings, item_warnings, confidences, invoice_date


def _similar(a: str, b: str) -> bool:
    from difflib import SequenceMatcher

    na, nb = normalise_text(a), normalise_text(b)
    return na == nb or SequenceMatcher(None, na, nb).ratio() >= 0.9


def _line_consistent(item: ExtractedInvoiceItem) -> bool:
    if item.quantity is None or item.unit_price is None or item.line_total is None:
        return False
    return approx_equal((item.quantity * item.unit_price).quantize(Decimal("0.01"), ROUND_HALF_UP), item.line_total)


def _quality(extraction: InvoiceExtraction) -> float:
    """Completeness + arithmetic-consistency score used to choose between rule and LLM results.

    Consistency matters: small models sometimes read a package size ("500 ml")
    as the quantity, which is grounded in the text but arithmetically wrong.
    """
    if not extraction.items:
        return 0.0
    count = len(extraction.items)
    complete = sum(1 for i in extraction.items if i.quantity is not None and i.line_total is not None) / count
    consistent = sum(1 for i in extraction.items if _line_consistent(i)) / count
    header = sum(1 for v in (extraction.invoice_number, extraction.total, extraction.supplier_name) if v) / 3
    return complete * 0.3 + consistent * 0.5 + header * 0.2 + min(count, 50) * 0.001


def extract_invoice(text: str, date_preference: str = "DMY", use_llm: bool | None = None) -> ExtractionOutcome:
    settings = get_settings()
    use_llm = settings.llm_enabled if use_llm is None else use_llm
    rules = parse_invoice_rules(text)
    chosen, method = rules, "rules"
    grounding_warnings: list[str] = []
    grounding_items: list[list[str]] = [[] for _ in rules.items]
    llm_error: str | None = None
    llm_failed = False

    if use_llm:
        try:
            llm_raw = extract_with_llm(text)
            grounded, grounding_warnings_llm, grounding_items_llm = ground_extraction(llm_raw, text)
            # Fill header gaps from deterministic parsing (both are grounded in the text).
            for name in ("supplier_name", "invoice_number", "invoice_date", "currency", "subtotal", "tax", "total"):
                if getattr(grounded, name) is None and getattr(rules, name) is not None:
                    setattr(grounded, name, getattr(rules, name))
            # Use the LLM result unless it is clearly worse than the rule parser.
            if grounded.items and _quality(grounded) >= _quality(rules) - 0.15:
                chosen, method = grounded, f"llm:{settings.ollama_model}"
                grounding_items = grounding_items_llm
                # Drop "discarded" notes where the deterministic parser found that same value.
                grounding_warnings = [
                    w
                    for w in grounding_warnings_llm
                    if not any(
                        getattr(chosen, name) is not None and w.startswith(f"Discarded {name} {getattr(chosen, name)}:")
                        for name in ("subtotal", "tax", "total")
                    )
                ]
            else:
                llm_error = "AI result was less complete or arithmetically inconsistent; rule-based result kept"
        except (LLMUnavailableError, OutputValidationError) as exc:
            llm_error, llm_failed = str(exc), True
            logger.info("LLM extraction unavailable, using rules: %s", exc)

    warnings, item_warnings, confidences, invoice_date = validate_extraction(chosen, date_preference)
    merged_items = [g + v for g, v in zip(grounding_items, item_warnings, strict=False)]
    if method == "rules":
        # Only a failed AI run is worth flagging. When the AI ran but the rule result was better, there is
        # nothing for the user to check (the comparison is kept in the audit log via `llm_error`).
        if llm_failed:
            warnings.insert(0, f"Read with rule-based parsing only; the local AI could not be used ({llm_error})")
        confidences = [round(c * 0.85, 2) for c in confidences]
    confidence = round(sum(confidences) / len(confidences), 2) if confidences else 0.0
    return ExtractionOutcome(
        extraction=chosen,
        method=method,
        warnings=grounding_warnings + warnings,
        item_warnings=merged_items,
        item_confidence=confidences,
        confidence=confidence,
        llm_error=llm_error,
        invoice_date_value=invoice_date,
    )
