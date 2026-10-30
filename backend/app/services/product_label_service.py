"""Packaging label parsing: OCR text (+ optional model hints) -> review fields.

Dates come exclusively from `date_extraction_service`. The language/vision
model may only suggest name/brand/size and transcribe date text; any date it
transcribes is re-parsed deterministically and always marked uncertain.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import date

from PIL import Image

from app.ai.llm_client import LLMUnavailableError, get_llm_client
from app.ai.output_validation import OutputValidationError, normalise_text, parse_structured, token_coverage
from app.ai.prompts import LABEL_SYSTEM, LABEL_USER
from app.core.config import get_settings
from app.schemas.extraction import LabelLLMHints
from app.services.date_extraction_service import DateField, FieldStatus, extract_dates
from app.services.ocr_service import OcrResult

logger = logging.getLogger(__name__)

_BATCH_RE = re.compile(
    r"\b(?:batch|lot|b\.?\s?no|batch\s*no|lot\s*no|bn|b/n|l/n)\b\.?\s*(?:no\.?|number|#)?\s*[:.#\-]?\s*([A-Z0-9][A-Z0-9\-/]{1,20})",
    re.I,
)
_SIZE_RE = re.compile(
    r"(?:net\s*(?:wt|weight|qty|quantity|vol(?:ume)?|content)s?\.?\s*[:\-]?\s*)?\b(\d+(?:[.,]\d+)?)\s?(ml|l|ltr|litre|liter|g|gm|gms|kg|oz|lb|pcs)\b",
    re.I,
)
_BRAND_RE = re.compile(r"\bbrand\s*[:\-]\s*([A-Za-z0-9&' .\-]{2,60})", re.I)
_NOT_NAME = re.compile(
    r"\b(ingredients?|nutrition|mfg|mfd|pkd|packed|exp|expiry|best|use\s*by|batch|lot|mrp|price|net|wt|weight|"
    r"manufactured|marketed|address|fssai|lic|customer|care|store|keep|refrigerat|veg|energy|protein|fat|"
    r"carbohydrate|sugar|sodium|incl|taxes|www|http|email|@)\b",
    re.I,
)


def _field(value, status: str, source: str | None = None, reasons: list[str] | None = None) -> dict:
    return {"value": value, "status": status, "source": source, "reasons": reasons or []}


def _guess_product_name(ocr: OcrResult) -> tuple[str | None, str | None]:
    """Pick the most prominent (tallest) plain-text line as a name suggestion."""
    candidates = []
    for line in ocr.lines:
        text = line.text.strip()
        letters = sum(c.isalpha() for c in text)
        if letters < 3 or letters < len(text) * 0.5 or _NOT_NAME.search(text) or len(text) > 60:
            continue
        candidates.append((line.height, -ocr.lines.index(line), text))
    if not candidates:
        return None, None
    candidates.sort(reverse=True)
    text = re.sub(r"\s{2,}", " ", candidates[0][2])
    return text, text


def _llm_hints(text: str, image_bytes: bytes | None) -> tuple[LabelLLMHints | None, str | None, str | None]:
    """Ask the local model for hints. Returns (hints, source, error)."""
    settings = get_settings()
    if not settings.llm_enabled:
        return None, None, None
    client = get_llm_client()
    schema = LabelLLMHints.model_json_schema()
    use_vision = bool(settings.ollama_vision_model and image_bytes)
    model = settings.ollama_vision_model if use_vision else settings.ollama_model
    try:
        if not client.has_model(model):
            return None, None, f"model {model} not installed"
        raw = client.chat_json(
            model=model,
            system=LABEL_SYSTEM,
            user=LABEL_USER.format(text=text[:4000] or "(no text recognised)"),
            schema=schema,
            images=[image_bytes] if use_vision and image_bytes else None,
        )
        return parse_structured(raw, LabelLLMHints), ("vision" if use_vision else "llm"), None
    except (LLMUnavailableError, OutputValidationError) as exc:
        return None, None, str(exc)


def _thumbnail_bytes(image: Image.Image) -> bytes:
    copy = image.copy()
    copy.thumbnail((1280, 1280))
    buffer = io.BytesIO()
    copy.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def parse_label(
    ocr: OcrResult,
    *,
    date_preference: str = "DMY",
    today: date | None = None,
    image: Image.Image | None = None,
    use_model: bool = True,
) -> dict:
    text = ocr.text
    today = today or date.today()
    dates = extract_dates(text, date_preference, today)
    warnings = list(dates.warnings)
    fields: dict[str, dict] = {}

    for key, result in dates.fields.items():
        fields[key] = _field(
            result.value.isoformat() if result.value else None,
            result.status.value,
            result.source_text,
            result.reasons,
        )
        fields[key]["options"] = [d.isoformat() for d in result.options]

    batch = _BATCH_RE.search(text)
    fields["batch_number"] = (
        _field(batch.group(1), FieldStatus.UNCERTAIN.value, batch.group(0), ["read by OCR; please verify"])
        if batch
        else _field(None, FieldStatus.NOT_FOUND.value)
    )
    size = _SIZE_RE.search(text)
    fields["package_size"] = (
        _field(f"{size.group(1)} {size.group(2).lower()}", FieldStatus.UNCERTAIN.value, size.group(0))
        if size
        else _field(None, FieldStatus.NOT_FOUND.value)
    )
    brand = _BRAND_RE.search(text)
    fields["brand"] = (
        _field(brand.group(1).strip(), FieldStatus.UNCERTAIN.value, brand.group(0))
        if brand
        else _field(None, FieldStatus.NOT_FOUND.value)
    )
    name, name_source = _guess_product_name(ocr)
    fields["product_name"] = (
        _field(name, FieldStatus.UNCERTAIN.value, name_source, ["most prominent text on the label"])
        if name
        else _field(None, FieldStatus.NOT_FOUND.value)
    )

    model_source = None
    if use_model:
        hints, model_source, model_error = _llm_hints(text, _thumbnail_bytes(image) if image is not None else None)
        if model_error:
            warnings.append(f"AI assistance unavailable ({model_error}); OCR-only results shown")
        if hints:
            _apply_hints(fields, hints, model_source or "llm", text, date_preference, today, warnings)

    return {
        "fields": fields,
        "date_candidates": dates.to_dict()["candidates"],
        "warnings": warnings,
        "ocr_confidence": ocr.mean_confidence,
        "model_source": model_source,
    }


def _apply_hints(
    fields: dict, hints: LabelLLMHints, source: str, text: str, preference: str, today: date, warnings: list[str]
) -> None:
    ocr_norm = normalise_text(text)
    for key in ("product_name", "brand", "package_size"):
        value = getattr(hints, key)
        if not value:
            continue
        grounded = token_coverage(ocr_norm, value) >= 0.5
        if source == "llm" and not grounded:
            continue  # a text-only model cannot know anything the OCR text doesn't contain
        if fields[key]["value"] is None or (key != "package_size" and grounded):
            reason = "suggested by the vision model" if source == "vision" else "suggested by the language model"
            fields[key] = _field(value, FieldStatus.UNCERTAIN.value, f"{source} model", [reason])

    if source == "vision" and hints.visible_date_text:
        vision_dates = extract_dates("\n".join(hints.visible_date_text), preference, today)
        for key, result in vision_dates.fields.items():
            if result.value and fields[key]["value"] is None:
                fields[key] = _field(
                    result.value.isoformat(),
                    FieldStatus.UNCERTAIN.value,
                    result.source_text,
                    [*result.reasons, "date text read by the vision model, not OCR; check it against the image"],
                )
                fields[key]["options"] = [d.isoformat() for d in result.options]
        if any(
            fields[k]["source"] and "vision" in str(fields[k]["reasons"])
            for k in (DateField.EXPIRY.value, DateField.BEST_BEFORE.value)
        ):
            warnings.append("Some dates were read by the vision model; verify them against the photo")
