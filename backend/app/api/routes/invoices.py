import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from app.api.deps import Auth, DbSession, Paging
from app.api.serializers import batches_out, invoice_summaries, tenant_today
from app.core.config import get_settings
from app.core.errors import ConflictError, ValidationFailed
from app.core.rate_limit import rate_limiter
from app.models.catalog import Product
from app.models.enums import InvoiceItemStatus, InvoiceStatus
from app.models.invoice import Invoice, InvoiceItem
from app.schemas.common import Page
from app.schemas.inventory import BatchOut
from app.schemas.invoice import (
    AddToInventoryRequest,
    InvoiceConfirm,
    InvoiceDeleted,
    InvoiceHeaderUpdate,
    InvoiceItemCreate,
    InvoiceItemOut,
    InvoiceItemUpdate,
    InvoiceOut,
    InvoiceSummary,
    InvoiceUploadResponse,
)
from app.services import inventory_service as inv
from app.services import processing_service
from app.services.audit_service import record_audit
from app.services.number_parsing import approx_equal
from app.services.storage_service import (
    delete_stored,
    read_upload,
    resolve_path,
    safe_filename,
    sha256,
    store_bytes,
    validate_content,
)

router = APIRouter(prefix="/api/invoices", tags=["invoices"])

ALLOWED_TYPES = {"application/pdf", "image/jpeg", "image/png"}
EDITABLE_STATUSES = {InvoiceStatus.AWAITING_REVIEW, InvoiceStatus.FAILED, InvoiceStatus.CONFIRMED}


def _check_upload_rate(ctx: Auth) -> None:
    if not rate_limiter.hit(f"upload:{ctx.user_id}", get_settings().upload_rate_limit_per_minute, 60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many uploads; please wait a minute")


def _invoice_out(db, ctx: Auth, invoice: Invoice) -> InvoiceOut:
    out = InvoiceOut.model_validate(invoice)
    out.item_count = len(invoice.items)
    items = []
    for item in invoice.items:
        item_out = InvoiceItemOut.model_validate(item)
        if item.product_id is None and item.status != InvoiceItemStatus.REJECTED:
            similar = inv.find_similar_products(db, ctx.business_id, item.product_name, limit=1)
            if similar:
                item_out.suggested_product_id = similar[0][0].id
                item_out.suggested_product_name = similar[0][0].name
        items.append(item_out)
    out.items = items
    return out


def _line_warnings(item: InvoiceItem) -> list[str]:
    notes = ["edited during review"]
    if item.quantity is not None and item.unit_price is not None and item.line_total is not None:
        expected = (item.quantity * item.unit_price).quantize(Decimal("0.01"), ROUND_HALF_UP)
        if not approx_equal(expected, item.line_total):
            notes.append(f"quantity × unit price = {expected}, but line total is {item.line_total}")
    if item.quantity is None:
        notes.append("quantity missing")
    return notes


@router.post("/upload", response_model=InvoiceUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_invoice(
    ctx: Auth, db: DbSession, file: Annotated[UploadFile, File(description="PDF, JPEG or PNG")]
) -> InvoiceUploadResponse:
    """Upload an invoice. Processing (OCR + extraction) runs in the background."""
    _check_upload_rate(ctx)
    data = await read_upload(file)
    content_type, ext = validate_content(data, ALLOWED_TYPES)
    digest = sha256(data)
    existing = db.scalar(select(Invoice).where(Invoice.business_id == ctx.business_id, Invoice.file_sha256 == digest))
    if existing is not None:
        return InvoiceUploadResponse(invoice=invoice_summaries(db, [existing])[0], duplicate=True)
    invoice = Invoice(
        business_id=ctx.business_id,
        uploaded_by_id=ctx.user_id,
        status=InvoiceStatus.UPLOADED,
        original_filename=safe_filename(file.filename),
        storage_path=store_bytes(ctx.business_id, "invoices", data, ext),
        content_type=content_type,
        file_size=len(data),
        file_sha256=digest,
        warnings=[],
        attempts=0,
    )
    db.add(invoice)
    db.flush()
    record_audit(
        db,
        business_id=ctx.business_id,
        user_id=ctx.user_id,
        action="invoice.uploaded",
        entity_type="invoice",
        entity_id=invoice.id,
        details={"filename": invoice.original_filename},
    )
    db.commit()
    processing_service.submit(processing_service.process_invoice, invoice.id)
    db.refresh(invoice)
    return InvoiceUploadResponse(invoice=invoice_summaries(db, [invoice])[0])


@router.get("", response_model=Page[InvoiceSummary])
def list_invoices(
    ctx: Auth,
    db: DbSession,
    paging: Paging,
    status_filter: Annotated[InvoiceStatus | None, Query(alias="status")] = None,
) -> Page[InvoiceSummary]:
    stmt = select(Invoice).where(Invoice.business_id == ctx.business_id)
    if status_filter:
        stmt = stmt.where(Invoice.status == status_filter)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Invoice.created_at.desc()).offset(paging.offset).limit(paging.page_size)).all()
    return Page(items=invoice_summaries(db, list(rows)), total=total, page=paging.page, page_size=paging.page_size)


@router.get("/{invoice_id}", response_model=InvoiceOut)
def get_invoice(invoice_id: uuid.UUID, ctx: Auth, db: DbSession) -> InvoiceOut:
    return _invoice_out(db, ctx, inv.get_owned(db, Invoice, invoice_id, ctx.business_id))


@router.get("/{invoice_id}/file", response_class=FileResponse)
def get_invoice_file(invoice_id: uuid.UUID, ctx: Auth, db: DbSession) -> FileResponse:
    invoice = inv.get_owned(db, Invoice, invoice_id, ctx.business_id)
    return FileResponse(
        resolve_path(invoice.storage_path),
        media_type=invoice.content_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )


@router.get("/{invoice_id}/items", response_model=list[InvoiceItemOut])
def get_invoice_items(invoice_id: uuid.UUID, ctx: Auth, db: DbSession) -> list[InvoiceItemOut]:
    return _invoice_out(db, ctx, inv.get_owned(db, Invoice, invoice_id, ctx.business_id)).items


@router.patch("/{invoice_id}", response_model=InvoiceOut)
def update_invoice_header(invoice_id: uuid.UUID, payload: InvoiceHeaderUpdate, ctx: Auth, db: DbSession) -> InvoiceOut:
    invoice = inv.get_owned(db, Invoice, invoice_id, ctx.business_id, lock=True)
    if invoice.status not in EDITABLE_STATUSES:
        raise ConflictError("The invoice is still being processed")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(invoice, key, value.upper() if key == "currency" and value else value)
    db.commit()
    return _invoice_out(db, ctx, invoice)


@router.post("/{invoice_id}/items", response_model=InvoiceItemOut, status_code=status.HTTP_201_CREATED)
def add_invoice_item(invoice_id: uuid.UUID, payload: InvoiceItemCreate, ctx: Auth, db: DbSession) -> InvoiceItemOut:
    """Manually add a line that extraction missed (or for fully manual entry)."""
    invoice = inv.get_owned(db, Invoice, invoice_id, ctx.business_id, lock=True)
    if invoice.status not in EDITABLE_STATUSES:
        raise ConflictError("The invoice is still being processed")
    next_line = (max((i.line_number for i in invoice.items), default=0)) + 1
    item = InvoiceItem(
        invoice_id=invoice.id,
        business_id=ctx.business_id,
        line_number=next_line,
        status=InvoiceItemStatus.ACCEPTED if invoice.status == InvoiceStatus.CONFIRMED else InvoiceItemStatus.PENDING,
        edited=True,
        warnings=["added manually"],
        quantity_added=Decimal(0),
        **payload.model_dump(),
    )
    db.add(item)
    db.commit()
    return InvoiceItemOut.model_validate(item)


@router.patch("/{invoice_id}/items/{item_id}", response_model=InvoiceItemOut)
def update_invoice_item(
    invoice_id: uuid.UUID, item_id: uuid.UUID, payload: InvoiceItemUpdate, ctx: Auth, db: DbSession
) -> InvoiceItemOut:
    invoice = inv.get_owned(db, Invoice, invoice_id, ctx.business_id)
    if invoice.status not in EDITABLE_STATUSES:
        raise ConflictError("The invoice is still being processed")
    item = db.scalar(
        select(InvoiceItem)
        .where(
            InvoiceItem.id == item_id, InvoiceItem.invoice_id == invoice.id, InvoiceItem.business_id == ctx.business_id
        )
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice item not found")
    if item.status == InvoiceItemStatus.ADDED:
        raise ConflictError("This line has already been added to inventory")
    changes = payload.model_dump(exclude_unset=True)
    new_status = changes.pop("status", None)
    if changes.get("quantity") is not None and changes["quantity"] < item.quantity_added:
        raise ValidationFailed(f"{item.quantity_added} units of this line are already in inventory")
    if new_status == InvoiceItemStatus.REJECTED and item.quantity_added > 0:
        raise ConflictError("Part of this line is already in inventory and cannot be rejected")
    for key, value in changes.items():
        setattr(item, key, value)
    if changes:
        item.edited = True
        item.warnings = _line_warnings(item)
    if new_status:
        item.status = new_status
    db.commit()
    return InvoiceItemOut.model_validate(item)


@router.post("/{invoice_id}/confirm", response_model=InvoiceOut)
def confirm_invoice(invoice_id: uuid.UUID, payload: InvoiceConfirm, ctx: Auth, db: DbSession) -> InvoiceOut:
    """Confirm reviewed header values, accept/reject lines and (by default) receive accepted lines into inventory."""
    invoice = inv.get_owned(db, Invoice, invoice_id, ctx.business_id, lock=True)
    if invoice.status not in (InvoiceStatus.AWAITING_REVIEW, InvoiceStatus.FAILED):
        raise ConflictError(f"An invoice in status '{invoice.status}' cannot be confirmed")
    header = payload.model_dump(
        exclude_unset=True, exclude={"rejected_item_ids", "allow_duplicate", "add_to_inventory"}
    )
    for key, value in header.items():
        setattr(invoice, key, value.upper() if key == "currency" and value else value)
    if invoice.invoice_number and not payload.allow_duplicate:
        duplicate = db.scalar(
            select(Invoice).where(
                Invoice.business_id == ctx.business_id,
                Invoice.id != invoice.id,
                Invoice.status == InvoiceStatus.CONFIRMED,
                func.lower(Invoice.invoice_number) == invoice.invoice_number.lower(),
                func.lower(func.coalesce(Invoice.supplier_name, "")) == (invoice.supplier_name or "").lower(),
            )
        )
        if duplicate:
            raise ConflictError(
                f"Invoice {invoice.invoice_number} from this supplier was already confirmed on "
                f"{duplicate.confirmed_at:%d %b %Y}. Confirm again with allow_duplicate to proceed."
            )
    if not invoice.items:
        raise ValidationFailed("Add at least one line item before confirming")
    rejected = set(payload.rejected_item_ids)
    for item in invoice.items:
        if item.id in rejected:
            if item.quantity_added > 0:
                raise ConflictError(f"Line {item.line_number} is already in inventory and cannot be rejected")
            item.status = InvoiceItemStatus.REJECTED
        elif item.status == InvoiceItemStatus.PENDING:
            item.status = InvoiceItemStatus.ACCEPTED
    supplier = inv.get_or_create_supplier(db, ctx.business_id, invoice.supplier_name)
    invoice.supplier_id = supplier.id if supplier else None
    invoice.status = InvoiceStatus.CONFIRMED
    invoice.confirmed_at = datetime.now(timezone.utc)
    invoice.confirmed_by_id = ctx.user_id
    if payload.add_to_inventory:
        inv.receive_confirmed_invoice(db, business_id=ctx.business_id, user_id=ctx.user_id, invoice=invoice)
    record_audit(
        db,
        business_id=ctx.business_id,
        user_id=ctx.user_id,
        action="invoice.confirmed",
        entity_type="invoice",
        entity_id=invoice.id,
        details={
            "invoice_number": invoice.invoice_number,
            "total": str(invoice.total) if invoice.total is not None else None,
        },
    )
    db.commit()
    return _invoice_out(db, ctx, invoice)


@router.post("/{invoice_id}/reject", response_model=InvoiceOut)
def reject_invoice(invoice_id: uuid.UUID, ctx: Auth, db: DbSession) -> InvoiceOut:
    """Mark an upload as not an invoice / not to be used (kept for audit)."""
    invoice = inv.get_owned(db, Invoice, invoice_id, ctx.business_id, lock=True)
    if invoice.status not in (InvoiceStatus.AWAITING_REVIEW, InvoiceStatus.FAILED):
        raise ConflictError(f"An invoice in status '{invoice.status}' cannot be rejected")
    invoice.status = InvoiceStatus.REJECTED
    record_audit(
        db,
        business_id=ctx.business_id,
        user_id=ctx.user_id,
        action="invoice.rejected",
        entity_type="invoice",
        entity_id=invoice.id,
    )
    db.commit()
    return _invoice_out(db, ctx, invoice)


@router.delete("/{invoice_id}", response_model=InvoiceDeleted)
def delete_invoice(invoice_id: uuid.UUID, ctx: Auth, db: DbSession) -> InvoiceDeleted:
    """Delete an upload, its lines and file, and remove any stock it added that is still on hand."""
    invoice = inv.get_owned(db, Invoice, invoice_id, ctx.business_id, lock=True)
    storage_path = invoice.storage_path
    cleared = inv.delete_invoice(db, business_id=ctx.business_id, user_id=ctx.user_id, invoice=invoice)
    db.commit()
    delete_stored(storage_path)
    return InvoiceDeleted(batches_cleared=cleared)


@router.post("/{invoice_id}/reprocess", response_model=InvoiceOut)
def reprocess_invoice(invoice_id: uuid.UUID, ctx: Auth, db: DbSession) -> InvoiceOut:
    """Run extraction again (e.g. after installing the local LLM). Discards unconfirmed edits."""
    invoice = inv.get_owned(db, Invoice, invoice_id, ctx.business_id, lock=True)
    if invoice.status not in (InvoiceStatus.AWAITING_REVIEW, InvoiceStatus.FAILED):
        raise ConflictError("Only unconfirmed invoices can be processed again")
    invoice.status = InvoiceStatus.UPLOADED
    invoice.attempts = 0
    invoice.error_message = None
    db.commit()
    processing_service.submit(processing_service.process_invoice, invoice.id)
    db.refresh(invoice)
    return _invoice_out(db, ctx, invoice)


@router.post(
    "/{invoice_id}/items/{item_id}/add-to-inventory",
    response_model=list[BatchOut],
    status_code=status.HTTP_201_CREATED,
)
def add_item_to_inventory(
    invoice_id: uuid.UUID, item_id: uuid.UUID, payload: AddToInventoryRequest, ctx: Auth, db: DbSession
) -> list[BatchOut]:
    """Create one or more inventory batches from a confirmed invoice line."""
    invoice = inv.get_owned(db, Invoice, invoice_id, ctx.business_id)
    if invoice.status != InvoiceStatus.CONFIRMED:
        raise ConflictError("Confirm the invoice before adding its items to inventory")
    item = db.scalar(
        select(InvoiceItem)
        .where(
            InvoiceItem.id == item_id, InvoiceItem.invoice_id == invoice.id, InvoiceItem.business_id == ctx.business_id
        )
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice item not found")
    if payload.product_id:
        product = inv.get_owned(db, Product, payload.product_id, ctx.business_id)
    else:
        assert payload.new_product is not None
        product = inv.create_product(db, ctx.business_id, payload.new_product, reuse_existing=True)
    batches = inv.receive_invoice_item(
        db,
        business_id=ctx.business_id,
        user_id=ctx.user_id,
        invoice=invoice,
        item=item,
        product=product,
        unit=payload.unit,
        batches=payload.batches,
        received_date=payload.received_date,
        pack_size=payload.pack_size,
        pack_unit=payload.pack_unit,
    )
    db.commit()
    today, soon_days = tenant_today(db, ctx.business)
    return batches_out(db, batches, today, soon_days)
