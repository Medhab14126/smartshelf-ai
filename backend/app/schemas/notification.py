import uuid
from datetime import date, datetime

from app.schemas.common import ORMModel


class NotificationOut(ORMModel):
    id: uuid.UUID
    batch_id: uuid.UUID
    kind: str
    offset_days: int
    expiry_date: date
    recipient_email: str
    status: str
    subject: str | None
    attempts: int
    last_error: str | None
    next_attempt_at: datetime | None
    sent_at: datetime | None
    created_at: datetime
    product_name: str | None = None
    batch_number: str | None = None
