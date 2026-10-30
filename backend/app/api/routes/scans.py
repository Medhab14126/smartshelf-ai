import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from app.api.deps import Auth, DbSession, Paging
from app.api.routes.invoices import _check_upload_rate
from app.core.errors import ConflictError, ValidationFailed
from app.models.catalog import Product, Supplier
from app.models.enums import ExpiryDateStatus, InvoiceItemStatus, InvoiceStatus, ScanStatus
from app.models.inventory import InventoryBatch
from app.models.invoice import Invoice, InvoiceItem
from app.models.scan import ProductScan
from app.schemas.common import Page
from app.schemas.inventory import ProductCreate
from app.schemas.scan import ScanConfirm, ScanDeleted, ScanOut
from app.services import inventory_service as inv
from app.services import processing_service
from app.services.audit_service import record_audit
from app.services.storage_service import (
    delete_stored,
    read_upload,
    resolve_path,
    safe_filename,
    sha256,
    store_bytes,
    validate_content,
)

router = APIRouter(prefix="/api/scans", tags=["product scans"])

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _scan_out(db, scan: ProductScan) -> ScanOut:
    out = ScanOut.model_validate(scan)
    matched = (scan.extraction or {}).get("matched_product_id")
    if matched:
        product = db.get(Product, uuid.UUID(matched))
        if product and product.business_id == scan.business_id:
            out.matched_product_id, out.matched_product_name = product.id, product.name
    return out


def _owned_item(db, business_id: uuid.UUID, item_id: uuid.UUID) -> InvoiceItem:
    item = db.scalar(
        select(InvoiceItem).where(InvoiceItem.id == item_id, InvoiceItem.business_id == business_id).with_for_update()
    )
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice item not found")
    return item


@router.post("/product", response_model=ScanOut, status_code=status.HTTP_201_CREATED)
async def upload_product_scan(
    ctx: Auth,
    db: DbSession,
    file: Annotated[UploadFile, File(description="JPEG, PNG or WEBP photo of the packaging")],
    product_id: Annotated[uuid.UUID | None, Form()] = None,
    batch_id: Annotated[uuid.UUID | None, Form()] = None,
    invoice_item_id: Annotated[uuid.UUID | None, Form()] = None,
    quantity: Annotated[Decimal | None, Form(gt=0)] = None,
) -> ScanOut:
    """Upload a packaging photo; OCR, barcode and date extraction run in the background."""
    _check_upload_rate(ctx)
    # Validate associations before storing anything.
    if product_id:
        inv.get_owned(db, Product, product_id, ctx.business_id)
    if batch_id:
        batch = inv.get_owned(db, InventoryBatch, batch_id, ctx.business_id)
        product_id = product_id or batch.product_id
    if invoice_item_id:
        _owned_item(db, ctx.business_id, invoice_item_id)
    data = await read_upload(file)
    content_type, ext = validate_content(data, ALLOWED_TYPES)
    scan = ProductScan(
        business_id=ctx.business_id,
        created_by_id=ctx.user_id,
        status=ScanStatus.UPLOADED,
        original_filename=safe_filename(file.filename),
        storage_path=store_bytes(ctx.business_id, "scans", data, ext),
        content_type=content_type,
        file_size=len(data),
        file_sha256=sha256(data),
        product_id=product_id,
        batch_id=batch_id,
        invoice_item_id=invoice_item_id,
        quantity_hint=quantity,
        warnings=[],
        attempts=0,
    )
    db.add(scan)
    db.commit()
    processing_service.submit(processing_service.process_scan, scan.id)
    db.refresh(scan)
    return _scan_out(db, scan)


@router.get("", response_model=Page[ScanOut])
def list_scans(ctx: Auth, db: DbSession, paging: Paging) -> Page[ScanOut]:
    stmt = select(ProductScan).where(ProductScan.business_id == ctx.business_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(ProductScan.created_at.desc()).offset(paging.offset).limit(paging.page_size)).all()
    return Page(items=[_scan_out(db, s) for s in rows], total=total, page=paging.page, page_size=paging.page_size)


@router.get("/{scan_id}", response_model=ScanOut)
def get_scan(scan_id: uuid.UUID, ctx: Auth, db: DbSession) -> ScanOut:
    return _scan_out(db, inv.get_owned(db, ProductScan, scan_id, ctx.business_id))


@router.get("/{scan_id}/image", response_class=FileResponse)
def get_scan_image(scan_id: uuid.UUID, ctx: Auth, db: DbSession) -> FileResponse:
    scan = inv.get_owned(db, ProductScan, scan_id, ctx.business_id)
    return FileResponse(
        resolve_path(scan.storage_path),
        media_type=scan.content_type,
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/{scan_id}", response_model=ScanDeleted)
def delete_scan(scan_id: uuid.UUID, ctx: Auth, db: DbSession) -> ScanDeleted:
    """Delete a scan and its photo, and remove remaining stock from a batch the scan created."""
    scan = inv.get_owned(db, ProductScan, scan_id, ctx.business_id, lock=True)
    storage_path = scan.storage_path
    cleared = inv.delete_scan(db, business_id=ctx.business_id, user_id=ctx.user_id, scan=scan)
    db.commit()
    delete_stored(storage_path)
    return ScanDeleted(batches_cleared=cleared)


@router.post("/{scan_id}/reprocess", response_model=ScanOut)
def reprocess_scan(scan_id: uuid.UUID, ctx: Auth, db: DbSession) -> ScanOut:
    scan = inv.get_owned(db, ProductScan, scan_id, ctx.business_id, lock=True)
    if scan.status not in (ScanStatus.AWAITING_CONFIRMATION, ScanStatus.FAILED):
        raise ConflictError("Only unconfirmed scans can be processed again")
    scan.status, scan.attempts, scan.error_message = ScanStatus.UPLOADED, 0, None
    db.commit()
    processing_service.submit(processing_service.process_scan, scan.id)
    db.refresh(scan)
    return _scan_out(db, scan)


@router.post("/{scan_id}/confirm", response_model=ScanOut)
def confirm_scan(scan_id: uuid.UUID, payload: ScanConfirm, ctx: Auth, db: DbSession) -> ScanOut:
    """Save reviewed values. The expiry date is only trusted when `confirm_expiry` is true."""
    scan = inv.get_owned(db, ProductScan, scan_id, ctx.business_id, lock=True)
    if scan.status not in (ScanStatus.AWAITING_CONFIRMATION, ScanStatus.FAILED):
        raise ConflictError(f"A scan in status '{scan.status}' cannot be confirmed")

    if payload.batch_id:
        batch: InventoryBatch = inv.get_owned(db, InventoryBatch, payload.batch_id, ctx.business_id, lock=True)
        product = batch.product
        if payload.expiry_date and payload.confirm_expiry:
            inv.confirm_batch_expiry(
                db,
                batch,
                user_id=ctx.user_id,
                expiry_date=payload.expiry_date,
                expiry_type=payload.expiry_type,
                manufacturing_date=payload.manufacturing_date,
                batch_number=payload.batch_number,
            )
        else:
            if payload.expiry_date:
                if batch.expiry_date_status == ExpiryDateStatus.CONFIRMED and batch.expiry_date != payload.expiry_date:
                    raise ValidationFailed(
                        "This batch already has a confirmed expiry date; confirm the new date to replace it"
                    )
                if batch.expiry_date_status != ExpiryDateStatus.CONFIRMED:
                    batch.expiry_date, batch.expiry_type = payload.expiry_date, payload.expiry_type
                    batch.expiry_date_status = ExpiryDateStatus.UNCONFIRMED
            if payload.manufacturing_date:
                batch.manufacturing_date = payload.manufacturing_date
            if payload.batch_number:
                batch.batch_number = payload.batch_number
        record_audit(
            db,
            business_id=ctx.business_id,
            user_id=ctx.user_id,
            action="batch.expiry_from_scan",
            entity_type="inventory_batch",
            entity_id=batch.id,
            details={
                "scan_id": str(scan.id),
                "expiry_date": str(payload.expiry_date),
                "confirmed": payload.confirm_expiry,
            },
        )
    else:
        if payload.product_id:
            product = inv.get_owned(db, Product, payload.product_id, ctx.business_id)
        else:
            assert payload.new_product is not None
            data = payload.new_product
            if scan.barcode and not data.barcode:
                data = ProductCreate(**{**data.model_dump(), "barcode": scan.barcode})
            product = inv.create_product(db, ctx.business_id, data, reuse_existing=True)
        invoice = item = None
        if payload.invoice_item_id:
            item = _owned_item(db, ctx.business_id, payload.invoice_item_id)
            invoice = db.get(Invoice, item.invoice_id)
            if invoice is None or invoice.status != InvoiceStatus.CONFIRMED:
                raise ConflictError("Confirm the invoice before adding its items to inventory")
            if item.status in (InvoiceItemStatus.REJECTED, InvoiceItemStatus.ADDED):
                raise ConflictError("This invoice line is rejected or already fully in inventory")
            if item.quantity is not None and Decimal(item.quantity_added) + payload.quantity > Decimal(item.quantity):  # type: ignore[operator]
                raise ValidationFailed("The quantity exceeds what remains on the invoice line")
        supplier = inv.get_or_create_supplier(db, ctx.business_id, payload.supplier_name)
        if supplier is None and invoice is not None and invoice.supplier_id:
            supplier = db.get(Supplier, invoice.supplier_id)
        assert payload.quantity is not None and payload.unit
        batch = inv.create_batch(
            db,
            business_id=ctx.business_id,
            user_id=ctx.user_id,
            product=product,
            quantity=payload.quantity,
            unit=payload.unit,
            supplier=supplier,
            pack_size=payload.pack_size,
            pack_unit=payload.pack_unit,
            batch_number=payload.batch_number,
            manufacturing_date=payload.manufacturing_date,
            expiry_date=payload.expiry_date,
            expiry_type=payload.expiry_type,
            expiry_confirmed=payload.confirm_expiry,
            purchase_price=item.unit_price if item else None,
            received_date=invoice.invoice_date if invoice else None,
            invoice_id=invoice.id if invoice else None,
            invoice_item_id=item.id if item else None,
            reason="Added from product scan",
        )
        if item is not None:
            item.quantity_added = Decimal(item.quantity_added) + payload.quantity  # type: ignore[operator]
            item.product_id = product.id
            if item.quantity is None or item.quantity_added >= item.quantity:
                item.status = InvoiceItemStatus.ADDED

    if scan.barcode and not product.barcode:
        product.barcode = scan.barcode
    scan.batch_id, scan.product_id = batch.id, product.id
    scan.confirmed = payload.model_dump(mode="json")
    scan.confirmed_at = datetime.now(timezone.utc)
    scan.confirmed_by_id = ctx.user_id
    scan.status = ScanStatus.CONFIRMED
    db.commit()
    return _scan_out(db, scan)
