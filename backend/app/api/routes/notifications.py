import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import Auth, DbSession, Paging
from app.api.serializers import notifications_out
from app.db.session import SessionLocal
from app.models.enums import NotificationStatus
from app.models.notification import Notification
from app.schemas.common import Page
from app.schemas.notification import NotificationOut
from app.services import inventory_service as inv
from app.services import processing_service
from app.services.audit_service import record_audit
from app.services.notification_service import dispatch_pending, retry_notification, schedule_reminders

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("", response_model=Page[NotificationOut])
def list_notifications(
    ctx: Auth,
    db: DbSession,
    paging: Paging,
    status_filter: Annotated[NotificationStatus | None, Query(alias="status")] = None,
    batch_id: uuid.UUID | None = None,
) -> Page[NotificationOut]:
    stmt = select(Notification).where(Notification.business_id == ctx.business_id)
    if status_filter:
        stmt = stmt.where(Notification.status == status_filter)
    if batch_id:
        stmt = stmt.where(Notification.batch_id == batch_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Notification.created_at.desc()).offset(paging.offset).limit(paging.page_size)).all()
    return Page(items=notifications_out(db, list(rows)), total=total, page=paging.page, page_size=paging.page_size)


@router.post("/{notification_id}/retry", response_model=NotificationOut)
def retry(notification_id: uuid.UUID, ctx: Auth, db: DbSession, force: bool = False) -> NotificationOut:
    """Queue a failed notification again (or, with force=true, deliberately resend a sent one)."""
    notification = inv.get_owned(db, Notification, notification_id, ctx.business_id, lock=True)
    retry_notification(db, notification, force=force)
    record_audit(
        db,
        business_id=ctx.business_id,
        user_id=ctx.user_id,
        action="notification.retry",
        entity_type="notification",
        entity_id=notification.id,
        details={"force": force},
    )
    db.commit()
    processing_service.submit(dispatch_pending, SessionLocal)
    db.refresh(notification)
    return notifications_out(db, [notification])[0]


@router.post("/run-check", response_model=dict)
def run_check_now(ctx: Auth, db: DbSession) -> dict:
    """Run the expiry check for this business immediately (normally scheduled)."""
    created = schedule_reminders(db, business_id=ctx.business_id)
    db.commit()
    processing_service.submit(dispatch_pending, SessionLocal)
    return {"scheduled": created}
