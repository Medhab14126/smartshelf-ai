from fastapi import APIRouter, HTTPException, status

from app.api.deps import Auth, DbSession
from app.core.config import get_settings
from app.models.account import Business
from app.models.enums import MembershipRole
from app.schemas.business import BusinessOut, BusinessUpdate, NotificationSettingsOut, NotificationSettingsUpdate
from app.services.audit_service import record_audit
from app.services.notification_service import get_notification_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _require_owner(ctx: Auth) -> None:
    if ctx.role != MembershipRole.OWNER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the business owner can change settings")


@router.get("/business", response_model=BusinessOut)
def get_business(ctx: Auth) -> BusinessOut:
    return BusinessOut.model_validate(ctx.business)


@router.put("/business", response_model=BusinessOut)
def update_business(payload: BusinessUpdate, ctx: Auth, db: DbSession) -> BusinessOut:
    _require_owner(ctx)
    business = db.get(Business, ctx.business_id)
    assert business is not None
    before = BusinessOut.model_validate(business).model_dump(mode="json")
    for key, value in payload.model_dump().items():
        setattr(business, key, value.strip() if isinstance(value, str) else value)
    record_audit(
        db,
        business_id=business.id,
        user_id=ctx.user_id,
        action="business.updated",
        entity_type="business",
        entity_id=business.id,
        details={"before": before, "after": payload.model_dump(mode="json")},
    )
    db.commit()
    return BusinessOut.model_validate(business)


def _settings_out(settings) -> NotificationSettingsOut:
    out = NotificationSettingsOut.model_validate(settings)
    out.smtp_configured = get_settings().smtp_configured
    return out


@router.get("/notifications", response_model=NotificationSettingsOut)
def get_notification_config(ctx: Auth, db: DbSession) -> NotificationSettingsOut:
    settings = get_notification_settings(db, ctx.business_id)
    db.commit()
    return _settings_out(settings)


@router.put("/notifications", response_model=NotificationSettingsOut)
def update_notification_config(
    payload: NotificationSettingsUpdate, ctx: Auth, db: DbSession
) -> NotificationSettingsOut:
    _require_owner(ctx)
    settings = get_notification_settings(db, ctx.business_id)
    settings.enabled = payload.enabled
    settings.recipient_emails = [str(e) for e in payload.recipient_emails]
    settings.reminder_days = payload.reminder_days
    settings.notify_on_expired = payload.notify_on_expired
    settings.send_hour_local = payload.send_hour_local
    record_audit(
        db,
        business_id=ctx.business_id,
        user_id=ctx.user_id,
        action="notification_settings.updated",
        entity_type="notification_settings",
        entity_id=None,
        details=payload.model_dump(mode="json"),
    )
    db.commit()
    return _settings_out(settings)
