import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPk
from app.models.enums import NotificationKind, NotificationStatus, check_in
from app.models.inventory import InventoryBatch

DEFAULT_REMINDER_DAYS = [7, 3, 1, 0]


class NotificationSettings(Timestamps, Base):
    __tablename__ = "notification_settings"
    __table_args__ = (CheckConstraint("send_hour_local BETWEEN 0 AND 23", name="send_hour"),)

    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    recipient_emails: Mapped[list[str]] = mapped_column(JSONB, default=list)
    reminder_days: Mapped[list[int]] = mapped_column(JSONB, default=lambda: list(DEFAULT_REMINDER_DAYS))
    notify_on_expired: Mapped[bool] = mapped_column(default=True)
    # Reminders are not emailed before this local hour (avoids 3 a.m. emails).
    send_hour_local: Mapped[int] = mapped_column(Integer, default=8)


class Notification(UUIDPk, Timestamps, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        # Idempotency key: one reminder per batch, recipient, interval and date.
        UniqueConstraint(
            "batch_id", "recipient_email", "kind", "offset_days", "expiry_date", name="uq_notification_key"
        ),
        CheckConstraint(check_in("status", NotificationStatus), name="status"),
        CheckConstraint(check_in("kind", NotificationKind), name="kind"),
        Index("ix_notifications_dispatch", "status", "next_attempt_at"),
        Index("ix_notifications_business_created", "business_id", "created_at"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_batches.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30))
    # Days before expiry this reminder represents (0 = on the day, -1 = expired).
    offset_days: Mapped[int] = mapped_column(Integer)
    expiry_date: Mapped[date] = mapped_column(Date)
    recipient_email: Mapped[str] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(20), default=NotificationStatus.PENDING)
    subject: Mapped[str | None] = mapped_column(String(300))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    context: Mapped[dict | None] = mapped_column(JSONB)

    batch: Mapped[InventoryBatch] = relationship()
