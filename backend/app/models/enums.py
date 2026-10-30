from enum import StrEnum


class MembershipRole(StrEnum):
    OWNER = "owner"
    STAFF = "staff"


class InvoiceStatus(StrEnum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    AWAITING_REVIEW = "awaiting_review"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    REJECTED = "rejected"


class InvoiceItemStatus(StrEnum):
    PENDING = "pending"  # extracted, not yet reviewed
    ACCEPTED = "accepted"  # confirmed as part of the invoice
    REJECTED = "rejected"  # user marked the line as wrong / not stock
    ADDED = "added_to_inventory"  # fully converted into inventory batches


class ScanStatus(StrEnum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    FAILED = "failed"


class ExpiryDateStatus(StrEnum):
    """Verification state of a batch's expiry date (stored)."""

    MISSING = "missing"
    UNCONFIRMED = "unconfirmed"
    CONFIRMED = "confirmed"


class ExpiryType(StrEnum):
    EXPIRY = "expiry"  # expiry / use-by: unsafe after this date
    BEST_BEFORE = "best_before"  # quality date


class ExpiryStatus(StrEnum):
    """Derived, user-facing expiry status (computed, never stored)."""

    DEPLETED = "depleted"
    NO_EXPIRY_DATE = "no_expiry_date"
    REQUIRES_CONFIRMATION = "requires_confirmation"
    EXPIRED = "expired"
    EXPIRING_TODAY = "expiring_today"
    EXPIRING_SOON = "expiring_soon"
    FRESH = "fresh"


class PackUnit(StrEnum):
    # Size of one pack; the batch quantity counts packs. Mirrors services/units.PACK_UNITS.
    G = "g"
    KG = "kg"
    ML = "ml"
    LTR = "ltr"


class MovementType(StrEnum):
    RECEIVED = "received"
    CONSUMED = "consumed"
    SOLD = "sold"
    DISCARDED = "discarded"
    RETURNED = "returned"
    CORRECTION = "correction"


# Movements that reduce stock; quantity in the request is always positive.
OUTGOING_MOVEMENTS = {MovementType.CONSUMED, MovementType.SOLD, MovementType.DISCARDED, MovementType.RETURNED}


class NotificationStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NotificationKind(StrEnum):
    EXPIRY_REMINDER = "expiry_reminder"
    EXPIRED = "expired"


def check_in(column: str, enum: type[StrEnum]) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum)
    return f"{column} IN ({values})"
