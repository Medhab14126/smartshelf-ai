from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import Auth, DbSession
from app.api.serializers import batches_out, invoice_summaries, movements_out, notifications_out, tenant_today
from app.core.config import get_settings
from app.models.catalog import Product
from app.models.enums import ExpiryDateStatus, ExpiryStatus
from app.models.inventory import InventoryBatch, InventoryMovement
from app.models.invoice import Invoice
from app.models.notification import Notification
from app.schemas.dashboard import DashboardSummary, UpcomingExpiryItem
from app.services.expiry_status import confirmed_active_expiring_within, expiry_status_sql
from app.services.notification_service import get_notification_settings

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
def summary(ctx: Auth, db: DbSession) -> DashboardSummary:
    today, soon_days = tenant_today(db, ctx.business)
    status_expr = expiry_status_sql(today, soon_days)
    base = InventoryBatch.business_id == ctx.business_id
    counts = dict(db.execute(select(status_expr, func.count()).where(base).group_by(status_expr)).all())
    status_counts = {s.value: int(counts.get(s.value, 0)) for s in ExpiryStatus}
    within_7 = db.scalar(select(func.count()).where(base, confirmed_active_expiring_within(today, 7))) or 0
    active = db.scalar(select(func.count()).where(base, InventoryBatch.current_quantity > 0)) or 0
    total_batches = sum(status_counts.values())

    recent_batches = (
        db.scalars(select(InventoryBatch).where(base).order_by(InventoryBatch.created_at.desc()).limit(5))
        .unique()
        .all()
    )
    recent_invoices = db.scalars(
        select(Invoice).where(Invoice.business_id == ctx.business_id).order_by(Invoice.created_at.desc()).limit(5)
    ).all()
    recent_moves = db.scalars(
        select(InventoryMovement)
        .where(InventoryMovement.business_id == ctx.business_id)
        .order_by(InventoryMovement.created_at.desc())
        .limit(8)
    ).all()
    recent_notifications = db.scalars(
        select(Notification)
        .where(Notification.business_id == ctx.business_id)
        .order_by(Notification.created_at.desc())
        .limit(5)
    ).all()
    settings = get_notification_settings(db, ctx.business_id)
    db.commit()
    return DashboardSummary(
        today=today,
        soon_days=soon_days,
        active_batches=active,
        expiring_within_7_days=within_7,
        expiring_today=status_counts[ExpiryStatus.EXPIRING_TODAY],
        expired_needing_attention=status_counts[ExpiryStatus.EXPIRED],
        requires_confirmation=status_counts[ExpiryStatus.REQUIRES_CONFIRMATION],
        no_expiry_date=status_counts[ExpiryStatus.NO_EXPIRY_DATE],
        status_counts=status_counts,
        recent_batches=batches_out(db, list(recent_batches), today, soon_days),
        recent_invoices=invoice_summaries(db, list(recent_invoices)),
        recent_movements=movements_out(db, list(recent_moves)),
        recent_notifications=notifications_out(db, list(recent_notifications)),
        notifications_configured=bool(settings.enabled and settings.recipient_emails),
        smtp_configured=get_settings().smtp_configured,
        is_empty=total_batches == 0 and not recent_invoices,
    )


Window = Literal["today", "3d", "7d", "expired", "missing", "unconfirmed", "upcoming"]


@router.get("/upcoming-expiry", response_model=list[UpcomingExpiryItem])
def upcoming_expiry(
    ctx: Auth,
    db: DbSession,
    window: Window = "upcoming",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[UpcomingExpiryItem]:
    """Batches needing attention. `upcoming` = expired, today and within the soon window."""
    today, soon_days = tenant_today(db, ctx.business)
    status_expr = expiry_status_sql(today, soon_days)
    batch = InventoryBatch
    stmt = (
        select(batch, status_expr.label("status"))
        .join(Product, Product.id == batch.product_id)
        .where(batch.business_id == ctx.business_id)
    )
    confirmed_active = (batch.current_quantity > 0) & (batch.expiry_date_status == ExpiryDateStatus.CONFIRMED)
    filters = {
        "today": confirmed_active & (batch.expiry_date == today),
        "3d": confirmed_active_expiring_within(today, 3),
        "7d": confirmed_active_expiring_within(today, 7),
        "expired": confirmed_active & (batch.expiry_date < today),
        "missing": (batch.current_quantity > 0) & batch.expiry_date.is_(None),
        "unconfirmed": (batch.current_quantity > 0) & (batch.expiry_date_status == ExpiryDateStatus.UNCONFIRMED),
        "upcoming": confirmed_active & (batch.expiry_date <= today + timedelta(days=soon_days)),
    }
    stmt = stmt.where(filters[window]).order_by(batch.expiry_date.asc().nulls_last(), Product.name).limit(limit)
    items = []
    for row, status in db.execute(stmt).unique().all():
        items.append(
            UpcomingExpiryItem(
                batch_id=row.id,
                product_name=row.product.name,
                batch_number=row.batch_number,
                current_quantity=row.current_quantity,
                unit=row.unit,
                expiry_date=row.expiry_date,
                expiry_type=row.expiry_type,
                days_until_expiry=(row.expiry_date - today).days if row.expiry_date else None,
                status=status,
                updated_at=row.updated_at,
            )
        )
    return items
