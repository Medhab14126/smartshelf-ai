"""Expiry reminder scheduling and email dispatch.

Two steps, both run by the background scheduler:

* `schedule_reminders` creates `Notification` rows for batches whose
  *confirmed* expiry date has reached a reminder interval. The unique key
  (batch, recipient, kind, interval, expiry date) plus INSERT ... ON CONFLICT
  DO NOTHING makes this idempotent, so restarts and repeated runs never
  duplicate reminders.
* `dispatch_pending` sends pending notifications over SMTP with bounded
  exponential backoff; failures are kept with their error for manual retry.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import redact
from app.models.account import Business
from app.models.enums import ExpiryDateStatus, NotificationKind, NotificationStatus
from app.models.inventory import InventoryBatch
from app.models.notification import DEFAULT_REMINDER_DAYS, Notification, NotificationSettings
from app.services.email_service import (
    EmailNotConfiguredError,
    OutgoingEmail,
    PermanentEmailError,
    SmtpEmailSender,
    TemporaryEmailError,
    get_email_sender,
)
from app.services.expiry_status import business_today, due_reminder_offset

logger = logging.getLogger(__name__)

RETRY_BACKOFF_MINUTES = [1, 5, 15, 60, 180]
EXPIRED_OFFSET = -1

_templates = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent.parent / "templates" / "emails"),
    autoescape=select_autoescape(["html"]),
)


def get_notification_settings(db: Session, business_id: uuid.UUID) -> NotificationSettings:
    settings = db.get(NotificationSettings, business_id)
    if settings is None:
        settings = NotificationSettings(
            business_id=business_id,
            enabled=True,
            recipient_emails=[],
            reminder_days=list(DEFAULT_REMINDER_DAYS),
            notify_on_expired=True,
            send_hour_local=8,
        )
        db.add(settings)
        db.flush()
    return settings


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


def schedule_reminders(db: Session, now: datetime | None = None, business_id: uuid.UUID | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    created = 0
    stmt = (
        select(NotificationSettings, Business)
        .join(Business, Business.id == NotificationSettings.business_id)
        .where(NotificationSettings.enabled.is_(True))
    )
    if business_id:
        stmt = stmt.where(NotificationSettings.business_id == business_id)
    for settings, business in db.execute(stmt).all():
        recipients = [r for r in settings.recipient_emails or [] if r]
        if not recipients:
            continue
        if now.astimezone(ZoneInfo(business.timezone)).hour < settings.send_hour_local:
            continue  # too early in the business's local day
        today = business_today(business.timezone, now)
        offsets = [d for d in settings.reminder_days or [] if d >= 0]
        horizon = today + timedelta(days=max(offsets, default=0))
        conditions = [InventoryBatch.expiry_date.between(today, horizon)] if offsets else []
        if settings.notify_on_expired:
            conditions.append(InventoryBatch.expiry_date < today)
        if not conditions:
            continue
        batches = (
            db.scalars(
                select(InventoryBatch).where(
                    InventoryBatch.business_id == business.id,
                    InventoryBatch.current_quantity > 0,
                    # Only confirmed dates drive normal reminders.
                    InventoryBatch.expiry_date_status == ExpiryDateStatus.CONFIRMED,
                    or_(*conditions),
                )
            )
            .unique()
            .all()
        )
        for batch in batches:
            assert batch.expiry_date is not None
            if batch.expiry_date < today:
                kind, offset = NotificationKind.EXPIRED, EXPIRED_OFFSET
            else:
                due = due_reminder_offset(batch.expiry_date, today, offsets)
                if due is None:
                    continue
                kind, offset = NotificationKind.EXPIRY_REMINDER, due
            for recipient in recipients:
                result = db.execute(
                    insert(Notification)
                    .values(
                        id=uuid.uuid4(),
                        business_id=business.id,
                        batch_id=batch.id,
                        kind=kind.value,
                        offset_days=offset,
                        expiry_date=batch.expiry_date,
                        recipient_email=recipient.lower(),
                        status=NotificationStatus.PENDING.value,
                        attempts=0,
                        next_attempt_at=now,
                    )
                    .on_conflict_do_nothing(constraint="uq_notification_key")
                    .returning(Notification.id)
                )
                created += len(result.all())
    db.flush()
    if created:
        logger.info("Scheduled %d expiry notification(s)", created)
    return created


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt_qty(value) -> str:
    text = f"{value:f}".rstrip("0").rstrip(".") if "." in f"{value:f}" else f"{value:f}"
    return text


def render_email(notification: Notification, batch: InventoryBatch, business: Business, today: date) -> OutgoingEmail:
    product = batch.product.name
    days_left = (notification.expiry_date - today).days
    date_label = "Best-before date" if batch.expiry_type == "best_before" else "Expiry date"
    if days_left < 0:
        subject = f"SmartShelf AI — {product} has expired"
        intro = f"The following inventory batch passed its recorded {date_label.lower()} {abs(days_left)} day(s) ago and is still in stock."
        advice = "Please check the product and record its disposal, return or other stock adjustment in SmartShelf AI."
        accent, when = "#b42318", f"Expired {abs(days_left)} day(s) ago"
    elif days_left == 0:
        subject = f"SmartShelf AI — {product} expires today"
        intro = f"The following inventory batch reaches its recorded {date_label.lower()} today."
        advice = "Please check the product today and use, rotate or remove it as appropriate."
        accent, when = "#c4320a", "Today"
    else:
        noun = "day" if days_left == 1 else "days"
        subject = f"SmartShelf AI — {product} expires in {days_left} {noun}"
        intro = f"The following inventory batch is approaching its recorded {date_label.lower()}."
        advice = "Please check the product and prioritise appropriate stock rotation."
        accent, when = "#2f6b4f", str(days_left)
    rows = [
        ("Product", product + (f" ({batch.product.brand})" if batch.product.brand else "")),
        ("Batch", batch.batch_number or "—"),
        ("Remaining quantity", f"{_fmt_qty(batch.current_quantity)} {batch.unit}"),
        (date_label, notification.expiry_date.strftime("%-d %B %Y")),
        ("Days remaining", when),
    ]
    context = {
        "subject": subject,
        "business_name": business.name,
        "intro": intro,
        "advice": advice,
        "rows": rows,
        "accent": accent,
        "batch_url": f"{get_settings().frontend_base_url.rstrip('/')}/inventory/{batch.id}",
    }
    return OutgoingEmail(
        to=notification.recipient_email,
        subject=subject,
        html=_templates.get_template("expiry_reminder.html").render(**context),
        text=_templates.get_template("expiry_reminder.txt").render(**context),
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _still_relevant(notification: Notification, batch: InventoryBatch | None) -> str | None:
    if batch is None:
        return "batch no longer exists"
    if batch.current_quantity <= 0:
        return "batch depleted"
    if batch.expiry_date_status != ExpiryDateStatus.CONFIRMED:
        return "expiry date is no longer confirmed"
    if batch.expiry_date != notification.expiry_date:
        return "expiry date changed"
    return None


def send_notification(db: Session, notification: Notification, sender: SmtpEmailSender, now: datetime) -> str:
    """Attempt delivery of one locked notification. Returns its new status."""
    max_attempts = get_settings().email_max_attempts
    batch = db.get(InventoryBatch, notification.batch_id)
    stale = _still_relevant(notification, batch)
    if stale:
        notification.status = NotificationStatus.CANCELLED
        notification.last_error = f"Cancelled: {stale}"
        return notification.status
    assert batch is not None
    business = db.get(Business, notification.business_id)
    assert business is not None
    email = render_email(notification, batch, business, business_today(business.timezone, now))
    notification.subject = email.subject
    notification.context = {"product": batch.product.name, "batch_number": batch.batch_number}
    notification.attempts += 1
    try:
        sender.send(email)
    except EmailNotConfiguredError as exc:
        notification.status = NotificationStatus.FAILED
        notification.last_error = str(exc)
        notification.next_attempt_at = None
    except PermanentEmailError as exc:
        notification.status = NotificationStatus.FAILED
        notification.last_error = redact(str(exc))
        notification.next_attempt_at = None
    except TemporaryEmailError as exc:
        notification.last_error = redact(str(exc))
        if notification.attempts >= max_attempts:
            notification.status = NotificationStatus.FAILED
            notification.next_attempt_at = None
        else:
            delay = RETRY_BACKOFF_MINUTES[min(notification.attempts - 1, len(RETRY_BACKOFF_MINUTES) - 1)]
            notification.next_attempt_at = now + timedelta(minutes=delay)
    else:
        notification.status = NotificationStatus.SENT
        notification.sent_at = now
        notification.last_error = None
        notification.next_attempt_at = None
    if notification.status == NotificationStatus.FAILED:
        logger.warning("Notification %s failed: %s", notification.id, notification.last_error)
    return notification.status


def dispatch_pending(
    session_factory, now: datetime | None = None, sender: SmtpEmailSender | None = None, limit: int = 100
) -> dict[str, int]:
    """Send due notifications, each in its own short transaction."""
    now = now or datetime.now(timezone.utc)
    sender = sender or get_email_sender()
    counts: dict[str, int] = {}
    with session_factory() as db:
        ids = db.scalars(
            select(Notification.id)
            .where(
                Notification.status == NotificationStatus.PENDING,
                or_(Notification.next_attempt_at.is_(None), Notification.next_attempt_at <= now),
            )
            .order_by(Notification.created_at)
            .limit(limit)
        ).all()
    for notification_id in ids:
        with session_factory() as db:
            notification = db.scalar(
                select(Notification)
                .where(Notification.id == notification_id, Notification.status == NotificationStatus.PENDING)
                .with_for_update(skip_locked=True)
            )
            if notification is None:
                continue  # handled by another worker meanwhile
            try:
                status = send_notification(db, notification, sender, now)
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Unexpected error while sending notification %s", notification_id)
                status = "error"
            counts[status] = counts.get(status, 0) + 1
    return counts


def retry_notification(db: Session, notification: Notification, *, force: bool = False) -> Notification:
    if notification.status == NotificationStatus.SENT and not force:
        from app.core.errors import ConflictError

        raise ConflictError("This notification was already sent; use force=true to resend it deliberately")
    if notification.status == NotificationStatus.PENDING:
        notification.next_attempt_at = datetime.now(timezone.utc)
        return notification
    notification.status = NotificationStatus.PENDING
    notification.attempts = 0  # a fresh bounded retry cycle
    notification.last_error = None
    notification.next_attempt_at = datetime.now(timezone.utc)
    return notification
