"""Background processing of uploaded invoices and product scans.

State machine (invoices): uploaded -> processing -> awaiting_review -> confirmed
                                               \\-> failed (manual entry or reprocess)
State machine (scans):    uploaded -> processing -> awaiting_confirmation -> confirmed
                                               \\-> failed

A record is claimed with an atomic UPDATE ... WHERE status='uploaded', so a
document is never processed twice concurrently. OCR/LLM work happens outside
any database transaction. Interrupted jobs (e.g. a restart mid-processing) are
re-queued by `sweep_stuck_jobs` up to PROCESSING_MAX_ATTEMPTS times.
"""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, select, update

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.account import Business
from app.models.catalog import Product
from app.models.enums import InvoiceItemStatus, InvoiceStatus, ScanStatus
from app.models.invoice import Invoice, InvoiceItem
from app.models.scan import ProductScan
from app.services.barcode_service import decode_barcodes
from app.services.expiry_status import business_today
from app.services.invoice_extraction_service import extract_invoice
from app.services.ocr_service import (
    DocumentReadError,
    OcrUnavailableError,
    extract_document_text,
    load_image,
    ocr_image,
)
from app.services.product_label_service import parse_label
from app.services.storage_service import resolve_path
from app.services.units import canonical_unit, pack_from_line

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="doc-processing")
# Tests set this to True to process synchronously.
INLINE_PROCESSING = False


def submit(fn, *args) -> None:
    if INLINE_PROCESSING:
        fn(*args)
    else:
        _executor.submit(_safe_run, fn, *args)


def _safe_run(fn, *args) -> None:
    try:
        fn(*args)
    except Exception:  # pragma: no cover - last-resort logging
        logger.exception("Background job %s failed", getattr(fn, "__name__", fn))


def _claim(model, record_id: uuid.UUID, processing_status: str) -> bool:
    with SessionLocal() as db:
        result = db.execute(
            update(model)
            .where(model.id == record_id, model.status == "uploaded")
            .values(
                status=processing_status,
                attempts=model.attempts + 1,
                processing_started_at=datetime.now(timezone.utc),
                error_message=None,
            )
        )
        db.commit()
        return result.rowcount == 1


def _fail(model, record_id: uuid.UUID, message: str) -> None:
    with SessionLocal() as db:
        db.execute(
            update(model)
            .where(model.id == record_id)
            .values(status="failed", error_message=message, processed_at=datetime.now(timezone.utc))
        )
        db.commit()


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


def process_invoice(invoice_id: uuid.UUID) -> None:
    if not _claim(Invoice, invoice_id, InvoiceStatus.PROCESSING):
        return
    with SessionLocal() as db:
        invoice = db.get(Invoice, invoice_id)
        assert invoice is not None
        business = db.get(Business, invoice.business_id)
        assert business is not None
        path, content_type, preference = resolve_path(invoice.storage_path), invoice.content_type, business.date_format

    try:
        document = extract_document_text(path, content_type)
        if len(document.text.strip()) < 10:
            raise DocumentReadError(
                "No readable text was found. Try a sharper, well-lit photo, or enter the invoice manually."
            )
        outcome = extract_invoice(document.text, preference)
    except OcrUnavailableError as exc:
        _fail(Invoice, invoice_id, f"OCR is not available on the server: {exc}. You can enter the invoice manually.")
        return
    except DocumentReadError as exc:
        _fail(Invoice, invoice_id, str(exc))
        return
    except Exception:
        logger.exception("Invoice %s processing failed", invoice_id)
        _fail(Invoice, invoice_id, "Processing failed unexpectedly. You can retry or enter the invoice manually.")
        return

    with SessionLocal() as db:
        invoice = db.get(Invoice, invoice_id, with_for_update=True)
        if invoice is None or invoice.status != InvoiceStatus.PROCESSING:
            return
        data = outcome.extraction
        invoice.raw_text = document.text
        invoice.page_count = document.page_count
        invoice.ocr_engine = document.engine
        invoice.extraction_method = outcome.method
        invoice.extraction_raw = {
            "extraction": data.model_dump(mode="json"),
            "method": outcome.method,
            "llm_error": outcome.llm_error,
            "ocr_confidence": document.mean_confidence,
        }
        invoice.supplier_name = data.supplier_name
        invoice.invoice_number = data.invoice_number
        invoice.invoice_date = outcome.invoice_date_value  # type: ignore[assignment]
        invoice.currency = data.currency
        invoice.subtotal = data.subtotal
        invoice.tax = data.tax
        invoice.total = data.total
        warnings = list(outcome.warnings)
        if document.mean_confidence is not None and document.mean_confidence < 0.6:
            warnings.append("OCR confidence is low; check every value carefully against the document")
        if data.invoice_number:
            duplicate = db.scalar(
                select(Invoice.id).where(
                    Invoice.business_id == invoice.business_id,
                    Invoice.id != invoice.id,
                    Invoice.invoice_number == data.invoice_number,
                    Invoice.status != InvoiceStatus.REJECTED,
                )
            )
            if duplicate:
                warnings.append(
                    f"Invoice number {data.invoice_number} has already been uploaded; this may be a duplicate"
                )
        invoice.warnings = warnings
        invoice.confidence = outcome.confidence

        db.execute(delete(InvoiceItem).where(InvoiceItem.invoice_id == invoice.id))
        for index, item in enumerate(data.items):
            pack = pack_from_line(item.product_name, item.unit)
            db.add(
                InvoiceItem(
                    invoice_id=invoice.id,
                    business_id=invoice.business_id,
                    line_number=index + 1,
                    product_name=item.product_name,
                    product_code=item.product_code,
                    quantity=item.quantity,
                    unit=canonical_unit(item.unit),  # raw spelling stays in `extracted`
                    pack_size=pack[0] if pack else None,
                    pack_unit=pack[1] if pack else None,
                    unit_price=item.unit_price,
                    line_total=item.line_total,
                    confidence=outcome.item_confidence[index] if index < len(outcome.item_confidence) else None,
                    warnings=outcome.item_warnings[index] if index < len(outcome.item_warnings) else [],
                    extracted=item.model_dump(mode="json"),
                    status=InvoiceItemStatus.PENDING,
                    quantity_added=Decimal(0),
                )
            )
        invoice.status = InvoiceStatus.AWAITING_REVIEW
        invoice.processed_at = datetime.now(timezone.utc)
        db.commit()
    logger.info("Invoice %s processed with %s (%d items)", invoice_id, outcome.method, len(data.items))


# ---------------------------------------------------------------------------
# Product scans
# ---------------------------------------------------------------------------


def process_scan(scan_id: uuid.UUID) -> None:
    if not _claim(ProductScan, scan_id, ScanStatus.PROCESSING):
        return
    with SessionLocal() as db:
        scan = db.get(ProductScan, scan_id)
        assert scan is not None
        business = db.get(Business, scan.business_id)
        assert business is not None
        path = resolve_path(scan.storage_path)
        preference, tz, business_id = business.date_format, business.timezone, business.id

    try:
        image = load_image(path)
        barcodes = decode_barcodes(image)
        ocr = ocr_image(image, "label")
        parsed = parse_label(ocr, date_preference=preference, today=business_today(tz), image=image)
    except OcrUnavailableError as exc:
        _fail(ProductScan, scan_id, f"OCR is not available on the server: {exc}. Enter the details manually.")
        return
    except DocumentReadError as exc:
        _fail(ProductScan, scan_id, str(exc))
        return
    except Exception:
        logger.exception("Scan %s processing failed", scan_id)
        _fail(ProductScan, scan_id, "Processing failed unexpectedly. You can retry or enter the details manually.")
        return

    with SessionLocal() as db:
        scan = db.get(ProductScan, scan_id, with_for_update=True)
        if scan is None or scan.status != ScanStatus.PROCESSING:
            return
        warnings = list(parsed["warnings"])
        if not ocr.text.strip():
            warnings.append(
                "No text could be read from the photo. Retake it closer, in good light, or enter details manually."
            )
        barcode = barcodes[0] if barcodes else None
        if barcode:
            scan.barcode, scan.barcode_format = barcode.value[:64], barcode.format[:30]
            if barcode.checksum_valid is False:
                warnings.append("Barcode check digit is invalid; it may have been misread")
            match = db.scalar(
                select(Product).where(Product.business_id == business_id, Product.barcode == barcode.value)
            )
            if match:
                parsed["matched_product_id"] = str(match.id)
                parsed["matched_by"] = "barcode"
        parsed["fields"]["barcode"] = {
            "value": barcode.value if barcode else None,
            "status": "detected"
            if barcode and barcode.checksum_valid is not False
            else ("uncertain" if barcode else "not_found"),
            "source": barcode.format if barcode else None,
            "reasons": ["a barcode identifies the product only; it does not prove the expiry date"] if barcode else [],
        }
        if "matched_product_id" not in parsed and parsed["fields"]["product_name"]["value"]:
            from app.services.inventory_service import find_similar_products

            similar = find_similar_products(db, business_id, parsed["fields"]["product_name"]["value"], limit=1)
            if similar and similar[0][1] >= 0.85:
                parsed["matched_product_id"] = str(similar[0][0].id)
                parsed["matched_by"] = "name"
        scan.raw_text = ocr.text
        scan.ocr_engine = ocr.engine
        scan.extraction = parsed
        scan.warnings = warnings
        scan.status = ScanStatus.AWAITING_CONFIRMATION
        scan.processed_at = datetime.now(timezone.utc)
        db.commit()


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


def sweep_stuck_jobs() -> None:
    """Re-queue uploads that were never processed or were interrupted."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(minutes=settings.processing_stale_minutes)
    for model, processing, func in (
        (Invoice, InvoiceStatus.PROCESSING, process_invoice),
        (ProductScan, ScanStatus.PROCESSING, process_scan),
    ):
        with SessionLocal() as db:
            stuck = db.scalars(
                select(model)
                .where(model.status == processing, model.processing_started_at < stale_before)
                .with_for_update(skip_locked=True)
            ).all()
            for record in stuck:
                if record.attempts >= settings.processing_max_attempts:
                    record.status = "failed"
                    record.error_message = (
                        "Processing was interrupted repeatedly. Retry later or enter the details manually."
                    )
                else:
                    record.status = "uploaded"
            db.commit()
            waiting = db.scalars(
                select(model.id).where(model.status == "uploaded", model.created_at < now - timedelta(seconds=30))
            ).all()
        for record_id in waiting:
            submit(func, record_id)
