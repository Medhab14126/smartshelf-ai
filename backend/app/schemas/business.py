import uuid
from zoneinfo import available_timezones

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.common import ORMModel

_TIMEZONES = available_timezones()


def validate_timezone(value: str) -> str:
    if value not in _TIMEZONES:
        raise ValueError("unknown timezone; use an IANA name such as Asia/Kolkata")
    return value


class BusinessOut(ORMModel):
    id: uuid.UUID
    name: str
    timezone: str
    currency: str
    date_format: str


class BusinessUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    timezone: str
    currency: str = Field(min_length=3, max_length=3)
    date_format: str = Field(pattern="^(DMY|MDY)$")

    _tz = field_validator("timezone")(lambda cls, v: validate_timezone(v))

    @field_validator("currency")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()


class NotificationSettingsOut(ORMModel):
    enabled: bool
    recipient_emails: list[str]
    reminder_days: list[int]
    notify_on_expired: bool
    send_hour_local: int
    smtp_configured: bool = False


class NotificationSettingsUpdate(BaseModel):
    enabled: bool = True
    recipient_emails: list[EmailStr] = Field(default_factory=list, max_length=20)
    reminder_days: list[int] = Field(min_length=1, max_length=10)
    notify_on_expired: bool = True
    send_hour_local: int = Field(default=8, ge=0, le=23)

    @field_validator("reminder_days")
    @classmethod
    def _valid_days(cls, value: list[int]) -> list[int]:
        if any(d < 0 or d > 365 for d in value):
            raise ValueError("reminder days must be between 0 and 365")
        return sorted(set(value), reverse=True)

    @field_validator("recipient_emails")
    @classmethod
    def _dedupe(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(str(v).lower() for v in value))
