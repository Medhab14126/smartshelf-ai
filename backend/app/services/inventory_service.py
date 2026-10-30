"""Products, suppliers, batches and the stock movement ledger.

All quantity changes go through `create_batch` / `adjust_stock`, which lock
the batch row, refuse negative stock and append an `InventoryMovement` plus
an audit event in the same transaction.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from difflib import SequenceMatcher

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationFailed
from app.models.catalog import Product, Supplier
from app.models.enums import (
    OUTGOING_MOVEMENTS,
    ExpiryDateStatus,
    ExpiryType,
    InvoiceItemStatus,
    InvoiceStatus,
    MovementType,
    NotificationStatus,
    ScanStatus,
)
from app.models.inventory import InventoryBatch, InventoryMovement
from app.models.invoice import Invoice, InvoiceItem
from app.models.notification import Notification
from app.models.scan import ProductScan
from app.schemas.inventory import BatchUpdate, ProductCreate, StockAdjustment
from app.schemas.invoice import BatchSplit
from app.services.audit_service import record_audit


def normalise_name(value: str | None) -> str:
    text = (value or "").lower()
    text = re.sub(r"(\d)\s+(ml|l|ltr|g|gm|kg|pcs|pc)\b", r"\1\2", text)  # "500 ml" -> "500ml"
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def product_match_key(name: str, brand: str | None) -> str:
    return f"{normalise_name(name)}|{normalise_name(brand)}"[:330]


def get_owned(db: Session, model, object_id: uuid.UUID, business_id: uuid.UUID, *, lock: bool = False):
    """Fetch a tenant-owned row or raise 404 (also for other tenants' rows)."""
    stmt = select(model).where(model.id == object_id, model.business_id == business_id)
    if lock:
        stmt = stmt.with_for_update(of=model)
    obj = db.scalars(stmt).unique().first()
    if obj is None:
        raise NotFoundError(f"{model.__name__.replace('Inventory', '')} not found")
    return obj


# ---------------------------------------------------------------------------
# Suppliers & products
# ---------------------------------------------------------------------------


def get_or_create_supplier(db: Session, business_id: uuid.UUID, name: str | None) -> Supplier | None:
    if not name or not name.strip():
        return None
    name = " ".join(name.split())[:200]
    key = normalise_name(name)[:200]
    supplier = db.scalar(select(Supplier).where(Supplier.business_id == business_id, Supplier.normalized_name == key))
    if supplier is None:
        supplier = Supplier(business_id=business_id, name=name, normalized_name=key)
        db.add(supplier)
        db.flush()
    return supplier


def create_product(
    db: Session, business_id: uuid.UUID, data: ProductCreate, *, reuse_existing: bool = False
) -> Product:
    key = product_match_key(data.name, data.brand)
    existing = db.scalar(select(Product).where(Product.business_id == business_id, Product.match_key == key))
    if existing is not None:
        if reuse_existing:
            # Fill in details the existing record lacks; never overwrite.
            for attr in ("category", "default_unit", "package_size", "barcode", "product_code"):
                if getattr(existing, attr) is None and getattr(data, attr):
                    setattr(existing, attr, getattr(data, attr))
            return existing
        raise ConflictError(f"A product named '{data.name}' already exists")
    product = Product(business_id=business_id, match_key=key, **data.model_dump())
    db.add(product)
    db.flush()
    return product


def update_product(db: Session, product: Product, changes: dict) -> Product:
    if "name" in changes and not (changes["name"] or "").strip():
        raise ValidationFailed("Product name must not be blank")
    for attr, value in changes.items():
        setattr(product, attr, value.strip() if isinstance(value, str) else value)
    new_key = product_match_key(product.name, product.brand)
    if new_key != product.match_key:
        clash = db.scalar(
            select(Product.id).where(
                Product.business_id == product.business_id, Product.match_key == new_key, Product.id != product.id
            )
        )
        if clash:
            raise ConflictError("Another product with this name and brand already exists")
        product.match_key = new_key
    db.flush()
    return product


def find_similar_products(
    db: Session, business_id: uuid.UUID, name: str, limit: int = 3
) -> list[tuple[Product, float]]:
    """Fuzzy product match used to suggest existing catalogue entries."""
    target = normalise_name(name)
    if not target:
        return []
    products = db.scalars(select(Product).where(Product.business_id == business_id)).all()
    scored = []
    for product in products:
        candidate = normalise_name(product.name)
        score = SequenceMatcher(None, target, candidate).ratio()
        if target == candidate:
            score = 1.0
        if score >= 0.75:
            scored.append((product, round(score, 3)))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:limit]


# ---------------------------------------------------------------------------
# Batches
# ---------------------------------------------------------------------------


def create_batch(
    db: Session,
    *,
    business_id: uuid.UUID,
    user_id: uuid.UUID | None,
    product: Product,
    quantity: Decimal,
    unit: str,
    supplier: Supplier | None = None,
    pack_size: Decimal | None = None,
    pack_unit: str | None = None,
    batch_number: str | None = None,
    manufacturing_date: date | None = None,
    expiry_date: date | None = None,
    expiry_type: ExpiryType = ExpiryType.EXPIRY,
    expiry_confirmed: bool = False,
    purchase_price: Decimal | None = None,
    received_date: date | None = None,
    notes: str | None = None,
    invoice_id: uuid.UUID | None = None,
    invoice_item_id: uuid.UUID | None = None,
    reason: str = "Stock received",
) -> InventoryBatch:
    if quantity <= 0:
        raise ValidationFailed("Quantity must be greater than zero")
    if manufacturing_date and expiry_date and expiry_date < manufacturing_date:
        raise ValidationFailed("Expiry date cannot be before the manufacturing date")
    status = ExpiryDateStatus.MISSING
    if expiry_date:
        status = ExpiryDateStatus.CONFIRMED if expiry_confirmed else ExpiryDateStatus.UNCONFIRMED
    batch = InventoryBatch(
        business_id=business_id,
        product_id=product.id,
        supplier_id=supplier.id if supplier else None,
        invoice_id=invoice_id,
        invoice_item_id=invoice_item_id,
        batch_number=batch_number,
        quantity_received=quantity,
        current_quantity=quantity,
        unit=unit.strip(),
        pack_size=pack_size if pack_unit else None,
        pack_unit=pack_unit if pack_size else None,
        manufacturing_date=manufacturing_date,
        expiry_date=expiry_date,
        expiry_type=expiry_type,
        expiry_date_status=status,
        expiry_confirmed_at=datetime.now(timezone.utc) if status == ExpiryDateStatus.CONFIRMED else None,
        expiry_confirmed_by_id=user_id if status == ExpiryDateStatus.CONFIRMED else None,
        purchase_price=purchase_price,
        received_date=received_date or date.today(),
        notes=notes,
        created_by_id=user_id,
    )
    db.add(batch)
    db.flush()
    db.add(
        InventoryMovement(
            business_id=business_id,
            batch_id=batch.id,
            movement_type=MovementType.RECEIVED,
            quantity_delta=quantity,
            quantity_after=quantity,
            reason=reason,
            created_by_id=user_id,
        )
    )
    record_audit(
        db,
        business_id=business_id,
        user_id=user_id,
        action="batch.created",
        entity_type="inventory_batch",
        entity_id=batch.id,
        details={
            "product": product.name,
            "quantity": str(quantity),
            "expiry_date": expiry_date.isoformat() if expiry_date else None,
        },
    )
    return batch


def receive_invoice_item(
    db: Session,
    *,
    business_id: uuid.UUID,
    user_id: uuid.UUID | None,
    invoice: Invoice,
    item: InvoiceItem,
    product: Product,
    unit: str,
    batches: list[BatchSplit],
    received_date: date | None = None,
    pack_size: Decimal | None = None,
    pack_unit: str | None = None,
) -> list[InventoryBatch]:
    """Turn (part of) a confirmed invoice line into batches; the caller holds the item row lock."""
    if item.status == InvoiceItemStatus.REJECTED:
        raise ConflictError("This line was rejected during review")
    if item.status == InvoiceItemStatus.ADDED:
        raise ConflictError("This line has already been added to inventory")
    requested = sum((b.quantity for b in batches), Decimal(0))
    if item.quantity is not None:
        remaining = Decimal(item.quantity) - Decimal(item.quantity_added)
        if requested > remaining:
            raise ValidationFailed(
                f"Only {remaining.normalize()} of this line remain to be added (requested {requested.normalize()})"
            )
    if item.product_code and not product.product_code:
        product.product_code = item.product_code
    supplier = db.get(Supplier, invoice.supplier_id) if invoice.supplier_id else None
    created = [
        create_batch(
            db,
            business_id=business_id,
            user_id=user_id,
            product=product,
            quantity=split.quantity,
            unit=unit,
            supplier=supplier,
            pack_size=pack_size if pack_size is not None else item.pack_size,
            pack_unit=pack_unit if pack_size is not None else item.pack_unit,
            batch_number=split.batch_number,
            manufacturing_date=split.manufacturing_date,
            expiry_date=split.expiry_date,
            expiry_type=split.expiry_type,
            expiry_confirmed=split.expiry_confirmed,
            purchase_price=item.unit_price,
            received_date=received_date or invoice.invoice_date,
            invoice_id=invoice.id,
            invoice_item_id=item.id,
            reason=f"Received on invoice {invoice.invoice_number or invoice.original_filename}",
        )
        for split in batches
    ]
    item.quantity_added = Decimal(item.quantity_added) + requested
    item.product_id = product.id
    if item.quantity is None or item.quantity_added >= item.quantity:
        item.status = InvoiceItemStatus.ADDED
    return created


def delete_invoice(db: Session, *, business_id: uuid.UUID, user_id: uuid.UUID | None, invoice: Invoice) -> int:
    """Delete an invoice and take whatever stock it added off the shelf; returns the number of batches cleared.

    Deleting usually means the upload was wrong or a duplicate, so stock it put into inventory is wrong too.
    The remaining quantity is removed with a "correction" movement (never by deleting batches), so the stock
    history still shows what happened; stock already used stays used. Batches keep existing, unlinked
    from the invoice by the foreign key's ON DELETE SET NULL.
    """
    if invoice.status == InvoiceStatus.PROCESSING:
        raise ConflictError("This invoice is still being read; try again in a moment")
    label = invoice.invoice_number or invoice.original_filename
    batch_ids = db.scalars(
        select(InventoryBatch.id).where(
            InventoryBatch.business_id == business_id, InventoryBatch.invoice_id == invoice.id
        )
    ).all()
    cleared = _clear_remaining_stock(db, business_id, user_id, batch_ids, f"Invoice {label} deleted")
    record_audit(
        db,
        business_id=business_id,
        user_id=user_id,
        action="invoice.deleted",
        entity_type="invoice",
        entity_id=invoice.id,
        details={
            "filename": invoice.original_filename,
            "invoice_number": invoice.invoice_number,
            "status": invoice.status,
            "batches_cleared": cleared,
        },
    )
    db.delete(invoice)
    return cleared


def delete_scan(db: Session, *, business_id: uuid.UUID, user_id: uuid.UUID | None, scan: ProductScan) -> int:
    """Delete a packaging scan; a batch the scan created loses its remaining stock, like a deleted invoice.

    A scan that only confirmed the date of an existing batch leaves that batch alone: the user checked
    the date against the package, and deleting the photo does not make that check untrue.
    """
    if scan.status == ScanStatus.PROCESSING:
        raise ConflictError("This scan is still being read; try again in a moment")
    created_batch = scan.batch_id is not None and not (scan.confirmed or {}).get("batch_id")
    batch_ids = [scan.batch_id] if created_batch and scan.batch_id else []
    cleared = _clear_remaining_stock(db, business_id, user_id, batch_ids, "Product scan deleted")
    record_audit(
        db,
        business_id=business_id,
        user_id=user_id,
        action="scan.deleted",
        entity_type="product_scan",
        entity_id=scan.id,
        details={"filename": scan.original_filename, "status": scan.status, "batches_cleared": cleared},
    )
    db.delete(scan)
    return cleared


def _clear_remaining_stock(
    db: Session, business_id: uuid.UUID, user_id: uuid.UUID | None, batch_ids, reason: str
) -> int:
    """Set each batch's remaining stock to zero with a recorded correction; returns how many had stock."""
    cleared = 0
    for batch_id in batch_ids:
        if Decimal(get_owned(db, InventoryBatch, batch_id, business_id).current_quantity) <= 0:
            continue
        adjust_stock(
            db,
            business_id=business_id,
            user_id=user_id,
            batch_id=batch_id,
            adjustment=StockAdjustment(movement_type=MovementType.CORRECTION, new_quantity=Decimal(0), reason=reason),
        )
        cleared += 1
    return cleared


# Quantities count packs or pieces; what a pack holds is its pack size (500 ml), not the unit.
COUNT_UNIT = "pcs"


def receive_confirmed_invoice(
    db: Session, *, business_id: uuid.UUID, user_id: uuid.UUID | None, invoice: Invoice
) -> list[InventoryBatch]:
    """Add every accepted line of a just-confirmed invoice to inventory, one batch per line.

    Confirming is the user's explicit decision to receive the delivery, so the lines become
    batches in the same transaction. A line without a quantity is left accepted for the user to
    add by hand. The quantity is counted in pcs, and the line's pack size (read from the product
    name, e.g. "Milk 500ml") goes on the batch. Batches carry no expiry date (invoices don't print one), so they show as
    "no expiry date" until a package is scanned or a date entered.
    """
    created: list[InventoryBatch] = []
    for item in sorted(invoice.items, key=lambda i: i.line_number):
        if item.status != InvoiceItemStatus.ACCEPTED:
            continue
        remaining = Decimal(item.quantity) - Decimal(item.quantity_added) if item.quantity is not None else None
        if remaining is None or remaining <= 0:
            note = "not added automatically: quantity missing; add it to inventory by hand"
            if note not in item.warnings:
                item.warnings = [*item.warnings, note]
            continue
        name = item.product_name[:200]
        # Exact name matches reuse the catalogue entry; fuzzy look-alikes are never merged silently.
        product = (
            get_owned(db, Product, item.product_id, business_id)
            if item.product_id
            else db.scalar(
                select(Product).where(
                    Product.business_id == business_id, Product.match_key == product_match_key(name, None)
                )
            )
        )
        if product is None:
            product = create_product(
                db, business_id, ProductCreate(name=name, default_unit=COUNT_UNIT, product_code=item.product_code)
            )
        created += receive_invoice_item(
            db,
            business_id=business_id,
            user_id=user_id,
            invoice=invoice,
            item=item,
            product=product,
            unit=COUNT_UNIT,
            batches=[BatchSplit(quantity=remaining)],
        )
    return created


def adjust_stock(
    db: Session, *, business_id: uuid.UUID, user_id: uuid.UUID | None, batch_id: uuid.UUID, adjustment: StockAdjustment
) -> InventoryMovement:
    # Row lock serialises concurrent adjustments to the same batch.
    batch: InventoryBatch = get_owned(db, InventoryBatch, batch_id, business_id, lock=True)
    current = Decimal(batch.current_quantity)
    movement_type = MovementType(adjustment.movement_type)

    if movement_type == MovementType.CORRECTION:
        assert adjustment.new_quantity is not None
        delta = adjustment.new_quantity - current
        if delta == 0:
            raise ValidationFailed("The counted quantity equals the current quantity; nothing to correct")
    elif movement_type in OUTGOING_MOVEMENTS:
        assert adjustment.quantity is not None
        delta = -adjustment.quantity
    else:  # RECEIVED: additional units for the same batch
        assert adjustment.quantity is not None
        delta = adjustment.quantity

    new_quantity = current + delta
    if new_quantity < 0:
        raise ValidationFailed(
            f"Cannot remove {abs(delta)} {batch.unit}: only {current.normalize()} {batch.unit} in stock"
        )
    batch.current_quantity = new_quantity
    if movement_type == MovementType.RECEIVED:
        batch.quantity_received = Decimal(batch.quantity_received) + delta
    movement = InventoryMovement(
        business_id=business_id,
        batch_id=batch.id,
        movement_type=movement_type,
        quantity_delta=delta,
        quantity_after=new_quantity,
        reason=(adjustment.reason or "").strip() or None,
        created_by_id=user_id,
    )
    db.add(movement)
    if new_quantity == 0:
        _cancel_pending_notifications(db, batch.id, "batch depleted")
    record_audit(
        db,
        business_id=business_id,
        user_id=user_id,
        action=f"stock.{movement_type.value}",
        entity_type="inventory_batch",
        entity_id=batch.id,
        details={"delta": str(delta), "quantity_after": str(new_quantity), "reason": movement.reason},
    )
    db.flush()
    return movement


def update_batch(
    db: Session, *, business_id: uuid.UUID, user_id: uuid.UUID | None, batch_id: uuid.UUID, data: BatchUpdate
) -> InventoryBatch:
    batch: InventoryBatch = get_owned(db, InventoryBatch, batch_id, business_id, lock=True)
    changes = data.model_dump(exclude_unset=True)
    before = {k: _jsonable(getattr(batch, k, None)) for k in changes if hasattr(batch, k)}

    if "supplier_name" in changes:
        supplier = get_or_create_supplier(db, business_id, changes.pop("supplier_name"))
        batch.supplier_id = supplier.id if supplier else None
    expiry_confirmed = changes.pop("expiry_confirmed", None)
    old_expiry, old_status = batch.expiry_date, batch.expiry_date_status
    if "pack_size" in changes or "pack_unit" in changes:
        batch.pack_size, batch.pack_unit = changes.pop("pack_size", None), changes.pop("pack_unit", None)
    for attr in (
        "batch_number",
        "unit",
        "manufacturing_date",
        "expiry_type",
        "purchase_price",
        "received_date",
        "notes",
    ):
        if attr in changes:
            value = changes[attr]
            if attr in ("unit",) and value is None:
                continue
            setattr(batch, attr, value.strip() if isinstance(value, str) else value)
    if "expiry_date" in changes:
        batch.expiry_date = changes["expiry_date"]
        if batch.expiry_date is None:
            batch.expiry_date_status = ExpiryDateStatus.MISSING
        elif batch.expiry_date != old_expiry:
            # A newly typed date is confirmed unless the user says otherwise.
            batch.expiry_date_status = (
                ExpiryDateStatus.UNCONFIRMED if expiry_confirmed is False else ExpiryDateStatus.CONFIRMED
            )
    if expiry_confirmed is not None and batch.expiry_date is not None:
        batch.expiry_date_status = ExpiryDateStatus.CONFIRMED if expiry_confirmed else ExpiryDateStatus.UNCONFIRMED
    if batch.expiry_date_status == ExpiryDateStatus.CONFIRMED and (
        old_status != ExpiryDateStatus.CONFIRMED or batch.expiry_date != old_expiry
    ):
        batch.expiry_confirmed_at = datetime.now(timezone.utc)
        batch.expiry_confirmed_by_id = user_id
    elif batch.expiry_date_status != ExpiryDateStatus.CONFIRMED:
        batch.expiry_confirmed_at = None
        batch.expiry_confirmed_by_id = None

    if batch.manufacturing_date and batch.expiry_date and batch.expiry_date < batch.manufacturing_date:
        raise ValidationFailed("Expiry date cannot be before the manufacturing date")
    if batch.expiry_date != old_expiry or batch.expiry_date_status != ExpiryDateStatus.CONFIRMED:
        _cancel_pending_notifications(db, batch.id, "expiry date changed or unconfirmed")

    record_audit(
        db,
        business_id=business_id,
        user_id=user_id,
        action="batch.updated",
        entity_type="inventory_batch",
        entity_id=batch.id,
        details={"before": before, "after": {k: _jsonable(getattr(batch, k, None)) for k in before}},
    )
    db.flush()
    return batch


def confirm_batch_expiry(
    db: Session,
    batch: InventoryBatch,
    *,
    user_id: uuid.UUID | None,
    expiry_date: date,
    expiry_type: ExpiryType,
    manufacturing_date: date | None,
    batch_number: str | None,
) -> None:
    if manufacturing_date and expiry_date < manufacturing_date:
        raise ValidationFailed("Expiry date cannot be before the manufacturing date")
    if batch.expiry_date != expiry_date:
        _cancel_pending_notifications(db, batch.id, "expiry date changed")
    batch.expiry_date = expiry_date
    batch.expiry_type = expiry_type
    batch.expiry_date_status = ExpiryDateStatus.CONFIRMED
    batch.expiry_confirmed_at = datetime.now(timezone.utc)
    batch.expiry_confirmed_by_id = user_id
    if manufacturing_date:
        batch.manufacturing_date = manufacturing_date
    if batch_number:
        batch.batch_number = batch_number


def _cancel_pending_notifications(db: Session, batch_id: uuid.UUID, reason: str) -> None:
    db.execute(
        update(Notification)
        .where(Notification.batch_id == batch_id, Notification.status == NotificationStatus.PENDING)
        .values(status=NotificationStatus.CANCELLED, last_error=f"Cancelled: {reason}")
    )


def _jsonable(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def product_stock_summary(
    db: Session, business_id: uuid.UUID, product_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, Decimal]]:
    if not product_ids:
        return {}
    rows = db.execute(
        select(InventoryBatch.product_id, func.count(), func.coalesce(func.sum(InventoryBatch.current_quantity), 0))
        .where(
            InventoryBatch.business_id == business_id,
            InventoryBatch.product_id.in_(product_ids),
            InventoryBatch.current_quantity > 0,
        )
        .group_by(InventoryBatch.product_id)
    ).all()
    return {pid: (count, Decimal(total)) for pid, count, total in rows}
