"""Single source of truth for batch expiry statuses and reminder offsets.

Status precedence (first match wins):

    depleted               current quantity is zero
    no_expiry_date         no date recorded
    requires_confirmation  a date exists but nobody has confirmed it
    expired                confirmed date is before today
    expiring_today         confirmed date is today
    expiring_soon          confirmed date within `soon_days`
    fresh                  otherwise

"Today" is always evaluated in the business's timezone. The same rules are
exposed as a SQL CASE expression so list filters and dashboard counts agree
exactly with the per-batch status.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, and_, case, literal

from app.models.enums import ExpiryDateStatus, ExpiryStatus
from app.models.inventory import InventoryBatch

DEFAULT_SOON_DAYS = 7


def business_today(timezone: str, now: datetime | None = None) -> date:
    now = now or datetime.now(ZoneInfo("UTC"))
    return now.astimezone(ZoneInfo(timezone)).date()


def soon_window(reminder_days: list[int] | None) -> int:
    """The expiring-soon window covers the largest configured reminder interval."""
    positive = [d for d in (reminder_days or []) if d > 0]
    return max(positive) if positive else DEFAULT_SOON_DAYS


def compute_expiry_status(
    *,
    current_quantity: Decimal | float,
    expiry_date: date | None,
    expiry_date_status: str,
    today: date,
    soon_days: int = DEFAULT_SOON_DAYS,
) -> ExpiryStatus:
    if current_quantity <= 0:
        return ExpiryStatus.DEPLETED
    if expiry_date is None or expiry_date_status == ExpiryDateStatus.MISSING:
        return ExpiryStatus.NO_EXPIRY_DATE
    if expiry_date_status != ExpiryDateStatus.CONFIRMED:
        return ExpiryStatus.REQUIRES_CONFIRMATION
    if expiry_date < today:
        return ExpiryStatus.EXPIRED
    if expiry_date == today:
        return ExpiryStatus.EXPIRING_TODAY
    if expiry_date <= today + timedelta(days=soon_days):
        return ExpiryStatus.EXPIRING_SOON
    return ExpiryStatus.FRESH


def expiry_status_sql(today: date, soon_days: int = DEFAULT_SOON_DAYS) -> ColumnElement[str]:
    batch = InventoryBatch
    return case(
        (batch.current_quantity <= 0, literal(ExpiryStatus.DEPLETED.value)),
        (batch.expiry_date.is_(None), literal(ExpiryStatus.NO_EXPIRY_DATE.value)),
        (
            batch.expiry_date_status != ExpiryDateStatus.CONFIRMED.value,
            literal(ExpiryStatus.REQUIRES_CONFIRMATION.value),
        ),
        (batch.expiry_date < today, literal(ExpiryStatus.EXPIRED.value)),
        (batch.expiry_date == today, literal(ExpiryStatus.EXPIRING_TODAY.value)),
        (batch.expiry_date <= today + timedelta(days=soon_days), literal(ExpiryStatus.EXPIRING_SOON.value)),
        else_=literal(ExpiryStatus.FRESH.value),
    )


def confirmed_active_expiring_within(today: date, days: int) -> ColumnElement[bool]:
    """Active batches with a confirmed date in [today, today + days]."""
    batch = InventoryBatch
    return and_(
        batch.current_quantity > 0,
        batch.expiry_date_status == ExpiryDateStatus.CONFIRMED.value,
        batch.expiry_date >= today,
        batch.expiry_date <= today + timedelta(days=days),
    )


def due_reminder_offset(expiry_date: date, today: date, reminder_days: list[int]) -> int | None:
    """Return the reminder interval that applies today, if any.

    Reminders fire when `days_left <= offset`. When several intervals are due
    at once (e.g. a batch added two days before expiry with intervals
    7/3/1/0), only the tightest due interval is sent, so staff get one useful
    email instead of a burst of stale ones. Expired batches return None; they
    are handled by the separate "expired" alert.
    """
    days_left = (expiry_date - today).days
    if days_left < 0:
        return None
    due = [offset for offset in set(reminder_days) if offset >= 0 and days_left <= offset]
    return min(due) if due else None
