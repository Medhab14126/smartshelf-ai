import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel

from app.schemas.inventory import BatchOut, MovementOut
from app.schemas.invoice import InvoiceSummary
from app.schemas.notification import NotificationOut


class DashboardSummary(BaseModel):
    today: date
    soon_days: int
    active_batches: int
    expiring_within_7_days: int
    expiring_today: int
    expired_needing_attention: int
    requires_confirmation: int
    no_expiry_date: int
    status_counts: dict[str, int]
    recent_batches: list[BatchOut]
    recent_invoices: list[InvoiceSummary]
    recent_movements: list[MovementOut]
    recent_notifications: list[NotificationOut]
    notifications_configured: bool
    smtp_configured: bool
    is_empty: bool


class UpcomingExpiryItem(BaseModel):
    batch_id: uuid.UUID
    product_name: str
    batch_number: str | None
    current_quantity: Decimal
    unit: str
    expiry_date: date | None
    expiry_type: str
    days_until_expiry: int | None
    status: str
    updated_at: datetime
