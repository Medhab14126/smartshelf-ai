"""Build response models that need computed fields or bulk lookups."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Business, User
from app.models.inventory import InventoryBatch, InventoryMovement
from app.models.invoice import Invoice
from app.models.notification import Notification
from app.models.scan import ProductScan
from app.schemas.inventory import BatchOut, MovementOut, ProductOut, SupplierOut
from app.schemas.invoice import InvoiceSummary
from app.schemas.notification import NotificationOut
from app.services.expiry_status import business_today, compute_expiry_status, soon_window
from app.services.notification_service import get_notification_settings


def tenant_today(db: Session, business: Business) -> tuple[date, int]:
    settings = get_notification_settings(db, business.id)
    return business_today(business.timezone), soon_window(settings.reminder_days)


def _user_names(db: Session, ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    return {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_(wanted)))}


def batches_out(db: Session, batches: list[InventoryBatch], today: date, soon_days: int) -> list[BatchOut]:
    if not batches:
        return []
    ids = [b.id for b in batches]
    invoice_ids = {b.invoice_id for b in batches if b.invoice_id}
    invoice_numbers = (
        dict(db.execute(select(Invoice.id, Invoice.invoice_number).where(Invoice.id.in_(invoice_ids))).all())
        if invoice_ids
        else {}
    )
    scans = dict(
        db.execute(
            select(ProductScan.batch_id, ProductScan.id)
            .where(ProductScan.batch_id.in_(ids))
            .order_by(ProductScan.batch_id, ProductScan.created_at.desc())
            .distinct(ProductScan.batch_id)
        ).all()
    )
    names = _user_names(db, (b.created_by_id for b in batches))
    out = []
    for b in batches:
        status = compute_expiry_status(
            current_quantity=b.current_quantity,
            expiry_date=b.expiry_date,
            expiry_date_status=b.expiry_date_status,
            today=today,
            soon_days=soon_days,
        )
        out.append(
            BatchOut.model_validate(
                {
                    **{
                        c: getattr(b, c)
                        for c in BatchOut.model_fields
                        if hasattr(b, c) and c not in ("product", "supplier", "status")
                    },
                    "product": ProductOut.model_validate(b.product),
                    "supplier": SupplierOut.model_validate(b.supplier) if b.supplier else None,
                    "status": status.value,
                    "days_until_expiry": (b.expiry_date - today).days if b.expiry_date else None,
                    "invoice_number": invoice_numbers.get(b.invoice_id) if b.invoice_id else None,
                    "image_scan_id": scans.get(b.id),
                    "created_by_name": names.get(b.created_by_id) if b.created_by_id else None,
                }
            )
        )
    return out


def movements_out(db: Session, movements: list[InventoryMovement]) -> list[MovementOut]:
    names = _user_names(db, (m.created_by_id for m in movements))
    out = []
    for m in movements:
        item = MovementOut.model_validate(m)
        item.created_by_name = names.get(m.created_by_id) if m.created_by_id else None
        item.product_name = m.batch.product.name if m.batch else None
        out.append(item)
    return out


def notifications_out(db: Session, notifications: list[Notification]) -> list[NotificationOut]:
    out = []
    for n in notifications:
        item = NotificationOut.model_validate(n)
        item.product_name = n.batch.product.name if n.batch else None
        item.batch_number = n.batch.batch_number if n.batch else None
        out.append(item)
    return out


def invoice_summaries(db: Session, invoices: list[Invoice]) -> list[InvoiceSummary]:
    from sqlalchemy import func

    from app.models.invoice import InvoiceItem

    ids = [i.id for i in invoices]
    counts = (
        dict(
            db.execute(
                select(InvoiceItem.invoice_id, func.count())
                .where(InvoiceItem.invoice_id.in_(ids))
                .group_by(InvoiceItem.invoice_id)
            ).all()
        )
        if ids
        else {}
    )
    out = []
    for inv in invoices:
        item = InvoiceSummary.model_validate(inv)
        item.item_count = counts.get(inv.id, 0)
        out.append(item)
    return out
