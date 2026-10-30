"""OCR with pluggable engines (PaddleOCR or Tesseract).

Both engines return word boxes which are regrouped into visual rows, so table
columns on an invoice end up on one text line regardless of the engine. To add
another engine, implement `OcrEngine.recognize_words()` and register it in
`_build_engine()`.
"""

from __future__ import annotations

import logging
import shutil
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class OcrUnavailableError(RuntimeError):
    """No OCR engine is installed / configured."""


class DocumentReadError(ValueError):
    """The document could not be opened or contains no readable text."""


@dataclass
class Word:
    text: str
    left: float
    top: float
    width: float
    height: float
    confidence: float | None = None


@dataclass
class OcrLine:
    text: str
    height: float
    confidence: float | None


@dataclass
class OcrResult:
    engine: str
    lines: list[OcrLine] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @property
    def mean_confidence(self) -> float | None:
        values = [line.confidence for line in self.lines if line.confidence is not None]
        return round(sum(values) / len(values), 3) if values else None


@dataclass
class DocumentText:
    pages: list[str]
    engine: str
    page_count: int
    used_text_layer: bool
    mean_confidence: float | None = None

    @property
    def text(self) -> str:
        return "\n\n".join(page for page in self.pages if page.strip())


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------


class OcrEngine:
    name = "base"

    def recognize_words(self, image: Image.Image, mode: str) -> list[Word]:  # pragma: no cover - interface
        raise NotImplementedError

    def recognize(self, image: Image.Image, mode: str = "document") -> OcrResult:
        words = self.recognize_words(image, mode)
        return OcrResult(engine=self.name, lines=group_words_into_lines(words))


class TesseractEngine(OcrEngine):
    name = "tesseract"

    def __init__(self, cmd: str | None, lang: str) -> None:
        import pytesseract

        self._tess = pytesseract
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        self.lang = lang

    def recognize_words(self, image: Image.Image, mode: str) -> list[Word]:
        # psm 4: variable-size text in columns (invoices); psm 11: sparse text (labels).
        psm = 4 if mode == "document" else 11
        data = self._tess.image_to_data(
            image, lang=self.lang, config=f"--psm {psm}", output_type=self._tess.Output.DICT
        )
        words: list[Word] = []
        for i, text in enumerate(data["text"]):
            text = (text or "").strip()
            conf = float(data["conf"][i])
            if not text or conf < 0:
                continue
            words.append(Word(text, data["left"][i], data["top"][i], data["width"][i], data["height"][i], conf / 100))
        return words

    def detect_rotation(self, image: Image.Image) -> int:
        """Return clockwise rotation (0/90/180/270) using Tesseract OSD, 0 on failure."""
        try:
            osd = self._tess.image_to_osd(image, config="--psm 0", output_type=self._tess.Output.DICT)
            if float(osd.get("orientation_conf", 0)) >= 2.0:
                return int(osd.get("rotate", 0))
        except Exception as exc:  # OSD fails on images with very little text
            logger.debug("orientation detection skipped: %s", exc)
        return 0


class PaddleEngine(OcrEngine):
    name = "paddleocr"

    def __init__(self, lang: str) -> None:
        from paddleocr import PaddleOCR  # optional dependency

        self._lock = threading.Lock()  # PaddleOCR predictors are not thread-safe
        try:  # PaddleOCR 3.x
            self._ocr = PaddleOCR(
                lang="en" if lang == "eng" else lang,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=True,
            )
            self._v3 = True
        except TypeError:  # PaddleOCR 2.x
            self._ocr = PaddleOCR(use_angle_cls=True, lang="en" if lang == "eng" else lang, show_log=False)
            self._v3 = False

    def recognize_words(self, image: Image.Image, mode: str) -> list[Word]:
        array = np.array(image.convert("RGB"))[:, :, ::-1]  # RGB -> BGR
        words: list[Word] = []
        with self._lock:
            if self._v3:
                for res in self._ocr.predict(array):
                    data = res.json.get("res", res) if hasattr(res, "json") else res
                    for text, score, poly in zip(
                        data["rec_texts"], data["rec_scores"], data["rec_polys"], strict=False
                    ):
                        words.append(_word_from_poly(text, score, poly))
            else:
                for page in self._ocr.ocr(array, cls=True) or []:
                    for poly, (text, score) in page or []:
                        words.append(_word_from_poly(text, score, poly))
        return [w for w in words if w.text.strip()]


def _word_from_poly(text: str, score: float, poly) -> Word:
    pts = np.array(poly, dtype=float).reshape(-1, 2)
    left, top = pts[:, 0].min(), pts[:, 1].min()
    return Word(str(text).strip(), left, top, pts[:, 0].max() - left, pts[:, 1].max() - top, float(score))


def group_words_into_lines(words: list[Word]) -> list[OcrLine]:
    """Cluster words into visual rows by vertical overlap, then sort by x.

    A wide horizontal gap is rendered as two spaces, which the invoice parser
    uses as a column separator hint.
    """
    if not words:
        return []
    words = sorted(words, key=lambda w: (w.top + w.height / 2, w.left))
    rows: list[list[Word]] = []
    for word in words:
        center = word.top + word.height / 2
        for row in rows:
            row_top = min(w.top for w in row)
            row_bottom = max(w.top + w.height for w in row)
            row_height = row_bottom - row_top
            if row_top - row_height * 0.15 <= center <= row_bottom + row_height * 0.15:
                row.append(word)
                break
        else:
            rows.append([word])

    lines: list[OcrLine] = []
    for row in sorted(rows, key=lambda r: min(w.top for w in r)):
        row.sort(key=lambda w: w.left)
        median_height = float(np.median([w.height for w in row]))
        parts = [row[0].text]
        for prev, cur in zip(row, row[1:], strict=False):
            gap = cur.left - (prev.left + prev.width)
            parts.append("  " if gap > median_height * 1.5 else " ")
            parts.append(cur.text)
        confidences = [w.confidence for w in row if w.confidence is not None]
        lines.append(
            OcrLine(
                text="".join(parts),
                height=median_height,
                confidence=round(sum(confidences) / len(confidences), 3) if confidences else None,
            )
        )
    return lines


def _paddle_installed() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("paddleocr") is not None
    except Exception:
        return False


def _build_engine() -> OcrEngine:
    settings = get_settings()
    choice = settings.ocr_engine.lower()
    if choice in ("paddle", "paddleocr") or (choice == "auto" and _paddle_installed()):
        try:
            return PaddleEngine(settings.tesseract_lang)
        except Exception as exc:
            if choice != "auto":
                raise OcrUnavailableError(f"PaddleOCR could not be initialised: {exc}") from exc
            logger.warning("PaddleOCR unavailable (%s); falling back to Tesseract", exc)
    if not (settings.tesseract_cmd or shutil.which("tesseract")):
        raise OcrUnavailableError("No OCR engine available: install Tesseract or PaddleOCR")
    return TesseractEngine(settings.tesseract_cmd, settings.tesseract_lang)


@lru_cache
def get_ocr_engine() -> OcrEngine:
    return _build_engine()


def ocr_status() -> dict:
    try:
        engine = get_ocr_engine()
        return {"available": True, "engine": engine.name}
    except OcrUnavailableError as exc:
        return {"available": False, "engine": None, "detail": str(exc)}


# ---------------------------------------------------------------------------
# Image preparation & document handling
# ---------------------------------------------------------------------------


def load_image(path: Path) -> Image.Image:
    try:
        image = Image.open(path)
        image = ImageOps.exif_transpose(image)
        return image.convert("RGB")
    except Exception as exc:
        raise DocumentReadError("The image could not be opened") from exc


def prepare_image(image: Image.Image) -> Image.Image:
    """Normalise size and contrast for OCR (the original file is kept as-is)."""
    longest = max(image.size)
    if longest < 1600:
        scale = 1600 / longest
        image = image.resize((int(image.width * scale), int(image.height * scale)), Image.Resampling.LANCZOS)
    elif longest > 4000:
        scale = 4000 / longest
        image = image.resize((int(image.width * scale), int(image.height * scale)), Image.Resampling.LANCZOS)
    gray = ImageOps.grayscale(image)
    return ImageOps.autocontrast(gray, cutoff=1)


def ocr_image(image: Image.Image, mode: str = "document") -> OcrResult:
    engine = get_ocr_engine()
    prepared = prepare_image(image)
    if isinstance(engine, TesseractEngine):
        rotation = engine.detect_rotation(prepared)
        if rotation:
            prepared = prepared.rotate(-rotation, expand=True)
    result = engine.recognize(prepared, mode)
    if mode == "label" and len(result.text) < 15 and isinstance(engine, TesseractEngine):
        # Sparse mode can miss text on busy packaging; retry in document mode.
        alt = engine.recognize(prepared, "document")
        if len(alt.text) > len(result.text):
            result = alt
    return result


def extract_document_text(path: Path, content_type: str, max_pages: int | None = None) -> DocumentText:
    """Return text for every page: PDF text layer first, OCR as fallback."""
    max_pages = max_pages or get_settings().max_pdf_pages
    if content_type == "application/pdf":
        return _extract_pdf(path, max_pages)
    result = ocr_image(load_image(path), "document")
    return DocumentText(
        pages=[result.text],
        engine=result.engine,
        page_count=1,
        used_text_layer=False,
        mean_confidence=result.mean_confidence,
    )


def _extract_pdf(path: Path, max_pages: int) -> DocumentText:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as exc:
        raise DocumentReadError("The PDF could not be opened (it may be corrupted or password-protected)") from exc
    try:
        page_count = len(pdf)
        if page_count == 0:
            raise DocumentReadError("The PDF has no pages")
        if page_count > max_pages:
            raise DocumentReadError(f"The PDF has {page_count} pages; the limit is {max_pages}")
        pages: list[str] = []
        engines: set[str] = set()
        confidences: list[float] = []
        used_text_layer = True
        for index in range(page_count):
            page = pdf[index]
            text_page = page.get_textpage()
            text = text_page.get_text_bounded() or ""
            text_page.close()
            if len(text.strip()) >= 20:
                pages.append(text.replace("\r\n", "\n").replace("\r", "\n"))
                engines.add("pdf-text")
            else:
                # Scanned page: render at ~300 dpi and OCR it.
                used_text_layer = False
                image = page.render(scale=300 / 72).to_pil().convert("RGB")
                result = ocr_image(image, "document")
                pages.append(result.text)
                engines.add(result.engine)
                if result.mean_confidence is not None:
                    confidences.append(result.mean_confidence)
            page.close()
        return DocumentText(
            pages=pages,
            engine="+".join(sorted(engines)),
            page_count=page_count,
            used_text_layer=used_text_layer,
            mean_confidence=round(sum(confidences) / len(confidences), 3) if confidences else None,
        )
    finally:
        pdf.close()
