"""Deterministic date extraction and classification for packaging/invoice text.

This module never guesses. Each date found in OCR text is:

1. Parsed with explicit format rules (no fuzzy parsing).
2. Flagged as *ambiguous* when more than one calendar interpretation is
   valid (e.g. 03/04/2026 may be 3 April or 4 March).
3. Classified only by the label printed next to it (MFG, PKD, EXP, USE BY,
   BEST BEFORE ...). Unlabelled dates stay "unknown"; the earliest/latest
   date is never assumed to be the expiry date.

The output is advisory: expiry fields always need explicit user
confirmation before they can drive reminders.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from enum import StrEnum

from dateutil.relativedelta import relativedelta


class DateField(StrEnum):
    MANUFACTURING = "manufacturing_date"
    PACKING = "packing_date"
    EXPIRY = "expiry_date"
    BEST_BEFORE = "best_before_date"
    UNKNOWN = "unknown"


class ParseStatus(StrEnum):
    VALID = "valid"  # exactly one calendar interpretation
    AMBIGUOUS = "ambiguous"  # several valid interpretations (day/month swap)
    PARTIAL = "partial"  # month + year only
    DERIVED = "derived"  # computed from "best before N months from mfg"
    INVALID = "invalid"  # looks like a date but is not a real calendar date


class FieldStatus(StrEnum):
    DETECTED = "detected"  # one labelled, unambiguous value
    UNCERTAIN = "uncertain"  # needs a human look (partial, derived, OCR-corrected, inconsistent)
    AMBIGUOUS = "ambiguous"  # conflicting values / interpretations
    NOT_FOUND = "not_found"


MIN_YEAR, MAX_YEAR = 2000, 2100

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
    "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}  # fmt: skip
_MONTH_RE = (
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
)
_SEP = r"[\s\-/.,']*"

# Ordered from most to least specific; earlier patterns claim their span first.
_DATE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ymd", re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")),
    ("dmy_numeric", re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})(?!\d)")),
    ("d_mon_y", re.compile(rf"(?<!\d)(\d{{1,2}})(?:st|nd|rd|th)?{_SEP}{_MONTH_RE}{_SEP}(\d{{4}}|\d{{2}})(?!\d)", re.I)),
    ("mon_d_y", re.compile(rf"\b{_MONTH_RE}{_SEP}(\d{{1,2}})(?:st|nd|rd|th)?[,\s\-/.]+(\d{{4}})(?!\d)", re.I)),
    ("mon_y", re.compile(rf"\b{_MONTH_RE}{_SEP}(\d{{4}}|\d{{2}})(?!\d)", re.I)),
    ("m_y_numeric", re.compile(r"(?<![\d/.\-])(\d{1,2})[-/.](\d{4})(?![\d/.\-])")),
    # MM/YY is only accepted next to a label (see _labelled_only).
    ("m_yy_numeric", re.compile(r"(?<![\d/.\-])(\d{1,2})[/.\-](\d{2})(?![\d/.\-])")),
]
_labelled_only = {"m_yy_numeric"}

# Label patterns. Best-before is matched before expiry so "best if used by"
# is not mistaken for "use by".
_LABEL_PATTERNS: list[tuple[DateField, re.Pattern[str]]] = [
    (
        DateField.BEST_BEFORE,
        re.compile(
            r"best\s*before(?:\s*end)?|best\s*by|best\s*if\s*used\s*(?:by|before)|\bbbe\b|\bb\.\s?b\.?|\bbb\b(?=[\s:.\-]*\d)",
            re.I,
        ),
    ),
    (
        DateField.EXPIRY,
        re.compile(
            r"use\s*by|use\s*before|expiry(?:\s*date)?|expiration(?:\s*date)?|expires?(?:\s*on)?"
            r"|\bexp\.?\s*(?:date|dt)?\b\.?|\bexp(?=[\s.:\-]*\d)|\bexpy\b",
            re.I,
        ),
    ),
    (
        DateField.MANUFACTURING,
        re.compile(
            r"\bmfg\.?(?:\s*(?:date|dt))?|\bmfd\.?(?:\s*on)?|manufactur(?:ed|ing|e)(?:\s*(?:on|date))?"
            r"|date\s*of\s*mfg|\bdom\b|\bmfgd\b|prod(?:uction)?\.?\s*date|produced(?:\s*on)?|\bmf\b(?=[\s.:\-]*\d)",
            re.I,
        ),
    ),
    (
        DateField.PACKING,
        re.compile(
            r"\bpkd\.?(?:\s*on)?|packed(?:\s*on)?|\bpkg\.?\s*(?:date|dt)|packing\s*date|packaging\s*date"
            r"|date\s*of\s*pack\w*|\bpack\s*date",
            re.I,
        ),
    ),
]

# "Best before 6 months from manufacture" style relative statements.
_RELATIVE_RE = re.compile(
    r"(best\s*before|use\s*(?:by|before|within)|expir\w*(?:\s*within)?|consume\s*within)\D{0,15}?"
    r"(\d{1,3})\s*(days?|weeks?|months?|years?)\s*(?:from|of|after)\s*(?:the\s*)?(?:date\s*of\s*)?"
    r"(mfg|mfd|manufactur\w*|pkd|pack\w*|production)",
    re.I,
)

# Common OCR confusions inside numeric date tokens.
_OCR_DIGIT_MAP = str.maketrans({"O": "0", "o": "0", "D": "0", "I": "1", "l": "1", "|": "1", "S": "5", "B": "8"})
_OCR_TOKEN_RE = re.compile(
    r"(?<![A-Za-z])[0-9OoDIl|SB]{1,4}[/.\-][0-9OoDIl|SB]{1,2}[/.\-][0-9OoDIl|SB]{2,4}(?![A-Za-z])"
)


@dataclass
class ParsedDate:
    raw: str
    status: ParseStatus
    value: date | None = None
    alternatives: list[date] = field(default_factory=list)
    precision: str = "day"  # "day" or "month"
    notes: list[str] = field(default_factory=list)
    # For numeric dates: which day/month orders produce a valid date.
    orders: dict[str, date] = field(default_factory=dict)


@dataclass
class DateCandidate:
    raw: str
    line: str
    line_index: int
    field: DateField
    label: str | None
    parsed: ParsedDate
    positional: bool = False  # label was on the previous line
    ocr_corrected: bool = False
    order_inferred: bool = False


@dataclass
class FieldResult:
    status: FieldStatus
    value: date | None = None
    options: list[date] = field(default_factory=list)
    source_text: str | None = None
    reasons: list[str] = field(default_factory=list)


@dataclass
class DateExtractionResult:
    fields: dict[str, FieldResult]
    candidates: list[DateCandidate]
    warnings: list[str]

    def to_dict(self) -> dict:
        def conv(obj):  # dataclass -> JSON-safe dict
            if isinstance(obj, date):
                return obj.isoformat()
            if isinstance(obj, dict):
                return {k: conv(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [conv(v) for v in obj]
            return obj

        return conv(
            {
                "fields": {name: asdict(result) for name, result in self.fields.items()},
                "candidates": [asdict(c) for c in self.candidates],
                "warnings": self.warnings,
            }
        )


# ---------------------------------------------------------------------------
# Single date parsing
# ---------------------------------------------------------------------------


def _expand_year(year_text: str, notes: list[str]) -> int:
    year = int(year_text)
    if len(year_text) == 2:
        notes.append("two-digit year interpreted as 20xx")
        year += 2000
    return year


def _safe_date(year: int, month: int, day: int) -> date | None:
    if not (MIN_YEAR <= year <= MAX_YEAR):
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _month_end(year: int, month: int) -> date | None:
    if not (MIN_YEAR <= year <= MAX_YEAR and 1 <= month <= 12):
        return None
    return date(year, month, calendar.monthrange(year, month)[1])


def _from_match(kind: str, groups: tuple[str, ...], raw: str, preference: str) -> ParsedDate:
    notes: list[str] = []
    if kind == "ymd":
        value = _safe_date(int(groups[0]), int(groups[1]), int(groups[2]))
        return ParsedDate(raw, ParseStatus.VALID if value else ParseStatus.INVALID, value)

    if kind == "dmy_numeric":
        a, b = int(groups[0]), int(groups[1])
        year = _expand_year(groups[2], notes)
        dmy = _safe_date(year, b, a)
        mdy = _safe_date(year, a, b)
        orders = {name: d for name, d in (("DMY", dmy), ("MDY", mdy)) if d}
        if not orders:
            return ParsedDate(raw, ParseStatus.INVALID, notes=notes)
        if dmy and mdy and dmy != mdy:
            preferred, other = (dmy, mdy) if preference == "DMY" else (mdy, dmy)
            return ParsedDate(raw, ParseStatus.AMBIGUOUS, preferred, [other], notes=notes, orders=orders)
        value = next(iter(orders.values()))
        if preference not in orders:
            notes.append("only valid when read in the non-preferred day/month order")
        if dmy == mdy:
            orders = {"DMY": value, "MDY": value}
        return ParsedDate(raw, ParseStatus.VALID, value, notes=notes, orders=orders)

    if kind == "d_mon_y":
        year = _expand_year(groups[2], notes)
        value = _safe_date(year, _MONTHS[groups[1].lower().rstrip(".")], int(groups[0]))
        return ParsedDate(raw, ParseStatus.VALID if value else ParseStatus.INVALID, value, notes=notes)

    if kind == "mon_d_y":
        value = _safe_date(int(groups[2]), _MONTHS[groups[0].lower().rstrip(".")], int(groups[1]))
        return ParsedDate(raw, ParseStatus.VALID if value else ParseStatus.INVALID, value, notes=notes)

    if kind in {"mon_y", "m_y_numeric", "m_yy_numeric"}:
        if kind == "mon_y":
            month, year_text = _MONTHS[groups[0].lower().rstrip(".")], groups[1]
        else:
            month, year_text = int(groups[0]), groups[1]
        year = _expand_year(year_text, notes)
        # Month-only dates: suggest the last day of the month, but never as a
        # confirmed value — the user must accept it.
        value = _month_end(year, month)
        if value is None:
            return ParsedDate(raw, ParseStatus.INVALID, notes=notes)
        notes.append("only month and year printed; last day of month suggested")
        return ParsedDate(raw, ParseStatus.PARTIAL, value, precision="month", notes=notes)

    raise ValueError(f"unknown pattern kind {kind}")


def _find_dates(line: str, preference: str, allow_labelled_only: bool) -> list[tuple[int, int, ParsedDate]]:
    taken: list[tuple[int, int]] = []
    found: list[tuple[int, int, ParsedDate]] = []
    for kind, pattern in _DATE_PATTERNS:
        if kind in _labelled_only and not allow_labelled_only:
            continue
        for match in pattern.finditer(line):
            start, end = match.span()
            if any(start < t_end and end > t_start for t_start, t_end in taken):
                continue
            parsed = _from_match(kind, match.groups(), match.group(0), preference)
            if kind in _labelled_only and parsed.status == ParseStatus.INVALID:
                continue  # "12/99" next to a label is more likely noise than a date
            taken.append((start, end))
            found.append((start, end, parsed))
    found.sort(key=lambda item: item[0])
    return found


def parse_date_string(text: str, preference: str = "DMY") -> ParsedDate | None:
    """Parse a single date value (e.g. an invoice date field)."""
    text = (text or "").strip()
    if not text:
        return None
    dates = _find_dates(text, preference, allow_labelled_only=False)
    return dates[0][2] if dates else None


# ---------------------------------------------------------------------------
# Labelled extraction from multi-line OCR text
# ---------------------------------------------------------------------------


def _correct_ocr_digits(line: str) -> tuple[str, bool]:
    changed = False

    def fix(match: re.Match[str]) -> str:
        nonlocal changed
        token = match.group(0)
        if sum(ch.isdigit() for ch in token) < 3:
            return token
        fixed = token.translate(_OCR_DIGIT_MAP)
        if fixed != token:
            changed = True
        return fixed

    return _OCR_TOKEN_RE.sub(fix, line), changed


def _find_labels(line: str) -> list[tuple[int, int, DateField, str]]:
    labels: list[tuple[int, int, DateField, str]] = []
    for field_name, pattern in _LABEL_PATTERNS:
        for match in pattern.finditer(line):
            start, end = match.span()
            if any(start < l_end and end > l_start for l_start, l_end, _, _ in labels):
                continue
            labels.append((start, end, field_name, match.group(0).strip()))
    labels.sort(key=lambda item: item[0])
    return labels


def _assign(events: list[tuple[str, int, object]]) -> list[tuple[object, tuple[DateField, str] | None]]:
    """Pair dates with labels in reading order.

    "MFG: d1 EXP: d2" -> d1=MFG, d2=EXP; "MFG/EXP: d1 d2" -> labels queued and
    paired in order. A date with no pending label stays unlabelled.
    """
    pending: list[tuple[DateField, str]] = []
    last_was_date = False
    out: list[tuple[object, tuple[DateField, str] | None]] = []
    for kind, _, payload in events:
        if kind == "label":
            if last_was_date:
                pending = []
            pending.append(payload)  # type: ignore[arg-type]
            last_was_date = False
        else:
            out.append((payload, pending.pop(0) if pending else None))
            last_was_date = True
    return out


def extract_dates(text: str, preference: str = "DMY", today: date | None = None) -> DateExtractionResult:
    today = today or date.today()
    lines = [ln.strip() for ln in (text or "").splitlines()]
    candidates: list[DateCandidate] = []
    warnings: list[str] = []
    carry_labels: list[tuple[DateField, str]] = []

    for index, original_line in enumerate(lines):
        if not original_line:
            continue
        line, corrected = _correct_ocr_digits(original_line)
        labels = _find_labels(line)
        dates = _find_dates(line, preference, allow_labelled_only=bool(labels or carry_labels))

        if labels and not dates:
            # Labels may sit on one line with their values on the next.
            carry_labels = [(f, raw) for _, _, f, raw in labels]
            continue

        events: list[tuple[str, int, object]] = [("label", s, (f, raw)) for s, _, f, raw in labels]
        events += [("date", s, (s, e, p)) for s, e, p in dates]
        events.sort(key=lambda ev: (ev[1], 0 if ev[0] == "label" else 1))

        positional = False
        if not labels and carry_labels and dates:
            events = [("label", -1, lbl) for lbl in carry_labels] + events
            positional = True
        carry_labels = []

        for (_start, _end, parsed), label in _assign(events):  # type: ignore[misc]
            if label is None and parsed.precision == "month" and not labels:
                continue  # bare "Mar 2026" text without a label is too weak
            candidates.append(
                DateCandidate(
                    raw=parsed.raw,
                    line=original_line,
                    line_index=index,
                    field=label[0] if label else DateField.UNKNOWN,
                    label=label[1] if label else None,
                    parsed=parsed,
                    positional=positional,
                    ocr_corrected=corrected,
                )
            )

    _apply_order_hint(candidates)
    candidates += _relative_candidates(text, candidates)
    fields = _resolve_fields(candidates, today, warnings)
    return DateExtractionResult(fields=fields, candidates=candidates, warnings=warnings)


def _apply_order_hint(candidates: list[DateCandidate]) -> None:
    """Resolve day/month swaps using other dates printed on the same label.

    If e.g. "MFG 25/10/2026" can only be DD/MM, an ambiguous "EXP 01/11/2026"
    on the same label very likely uses the same order. The result is still
    marked uncertain so the user confirms it.
    """
    unambiguous_orders = {
        next(iter(c.parsed.orders))
        for c in candidates
        if c.parsed.status == ParseStatus.VALID and len(c.parsed.orders) == 1
    }
    if len(unambiguous_orders) != 1:
        return
    order = unambiguous_orders.pop()
    for cand in candidates:
        parsed = cand.parsed
        if parsed.status == ParseStatus.AMBIGUOUS and order in parsed.orders:
            chosen = parsed.orders[order]
            others = [d for d in parsed.orders.values() if d != chosen]
            parsed.value, parsed.alternatives = chosen, others
            parsed.status = ParseStatus.VALID
            parsed.notes.append(
                f"day/month order inferred from other dates on the label ({'DD/MM' if order == 'DMY' else 'MM/DD'})"
            )
            cand.order_inferred = True


def _relative_candidates(text: str, candidates: list[DateCandidate]) -> list[DateCandidate]:
    """Derive best-before dates stated relative to MFG/PKD dates."""
    derived: list[DateCandidate] = []
    for match in _RELATIVE_RE.finditer(text or ""):
        amount, unit, base_word = int(match.group(2)), match.group(3).lower(), match.group(4).lower()
        base_field = DateField.PACKING if base_word.startswith(("pk", "pack")) else DateField.MANUFACTURING
        bases = {c.parsed.value for c in candidates if c.field == base_field and c.parsed.status == ParseStatus.VALID}
        target = (
            DateField.EXPIRY
            if match.group(1).lower().startswith(("use", "expir", "consume"))
            else DateField.BEST_BEFORE
        )
        if len(bases) != 1:
            continue
        base = bases.pop()
        assert base is not None
        if unit.startswith("day"):
            value = base + timedelta(days=amount)
        elif unit.startswith("week"):
            value = base + timedelta(weeks=amount)
        elif unit.startswith("month"):
            value = base + relativedelta(months=amount)
        else:
            value = base + relativedelta(years=amount)
        derived.append(
            DateCandidate(
                raw=match.group(0),
                line=match.group(0),
                line_index=-1,
                field=target,
                label=match.group(1),
                parsed=ParsedDate(
                    raw=match.group(0),
                    status=ParseStatus.DERIVED,
                    value=value,
                    notes=[f"calculated as {amount} {unit} after the {base_field.value.replace('_', ' ')}"],
                ),
            )
        )
    return derived


def _resolve_fields(candidates: list[DateCandidate], today: date, warnings: list[str]) -> dict[str, FieldResult]:
    fields: dict[str, FieldResult] = {}
    for date_field in (DateField.MANUFACTURING, DateField.PACKING, DateField.EXPIRY, DateField.BEST_BEFORE):
        own = [c for c in candidates if c.field == date_field and c.parsed.status != ParseStatus.INVALID]
        invalid = [c for c in candidates if c.field == date_field and c.parsed.status == ParseStatus.INVALID]
        if not own:
            reasons = [f"'{c.raw}' is not a valid calendar date" for c in invalid]
            fields[date_field.value] = FieldResult(
                FieldStatus.UNCERTAIN if invalid else FieldStatus.NOT_FOUND,
                source_text=invalid[0].line if invalid else None,
                reasons=reasons,
            )
            continue

        options: list[date] = []
        for cand in own:
            for value in [cand.parsed.value, *cand.parsed.alternatives]:
                if value and value not in options:
                    options.append(value)
        primary_values = {c.parsed.value for c in own}
        reasons: list[str] = []
        status = FieldStatus.DETECTED
        if len(primary_values) > 1:
            status = FieldStatus.AMBIGUOUS
            reasons.append("several different dates carry this label")
        for cand in own:
            if cand.parsed.status == ParseStatus.AMBIGUOUS:
                status = FieldStatus.AMBIGUOUS
                reasons.append(f"'{cand.raw}' could be read with day and month swapped")
            elif cand.parsed.status in (ParseStatus.PARTIAL, ParseStatus.DERIVED) and status == FieldStatus.DETECTED:
                status = FieldStatus.UNCERTAIN
            reasons.extend(cand.parsed.notes)
            if cand.ocr_corrected:
                reasons.append("OCR characters were corrected to digits")
                status = FieldStatus.UNCERTAIN if status == FieldStatus.DETECTED else status
            if cand.order_inferred and status == FieldStatus.DETECTED:
                status = FieldStatus.UNCERTAIN
            if cand.positional:
                reasons.append("label printed on the line above the date")
                status = FieldStatus.UNCERTAIN if status == FieldStatus.DETECTED else status
        fields[date_field.value] = FieldResult(
            status=status,
            value=own[0].parsed.value,
            options=options,
            source_text=own[0].line,
            reasons=list(dict.fromkeys(reasons)),
        )

    # Cross-field sanity checks.
    mfg = fields[DateField.MANUFACTURING.value]
    for exp_key in (DateField.EXPIRY.value, DateField.BEST_BEFORE.value):
        exp = fields[exp_key]
        if mfg.value and exp.value and exp.value <= mfg.value:
            warnings.append(f"{exp_key.replace('_', ' ')} is not after the manufacturing date")
            for result in (mfg, exp):
                result.status = FieldStatus.UNCERTAIN if result.status == FieldStatus.DETECTED else result.status
                result.reasons.append("manufacturing date must be before the expiry date")
        if exp.value and exp.value > today + timedelta(days=365 * 10):
            exp.status = FieldStatus.UNCERTAIN if exp.status == FieldStatus.DETECTED else exp.status
            exp.reasons.append("date is more than 10 years in the future")
        if exp.value and exp.value < today:
            warnings.append(f"{exp_key.replace('_', ' ')} {exp.value.isoformat()} is already in the past")
    for key in (DateField.MANUFACTURING.value, DateField.PACKING.value):
        result = fields[key]
        if result.value and result.value > today + timedelta(days=1):
            result.status = FieldStatus.UNCERTAIN if result.status == FieldStatus.DETECTED else result.status
            result.reasons.append("date is in the future")

    unlabelled = [c for c in candidates if c.field == DateField.UNKNOWN]
    if unlabelled:
        warnings.append(
            f"{len(unlabelled)} date(s) found without a recognisable label; choose the right one manually if needed"
        )
    return fields
