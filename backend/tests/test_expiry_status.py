from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.models.enums import ExpiryStatus
from app.services.expiry_status import business_today, compute_expiry_status, due_reminder_offset, soon_window

TODAY = date(2026, 10, 29)


def status(qty=5, expiry=None, verification="confirmed", soon=7):
    return compute_expiry_status(
        current_quantity=Decimal(qty), expiry_date=expiry, expiry_date_status=verification, today=TODAY, soon_days=soon
    )


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"qty": 0, "expiry": date(2026, 10, 1)}, ExpiryStatus.DEPLETED),
        ({"expiry": None, "verification": "missing"}, ExpiryStatus.NO_EXPIRY_DATE),
        ({"expiry": date(2026, 10, 1), "verification": "unconfirmed"}, ExpiryStatus.REQUIRES_CONFIRMATION),
        ({"expiry": date(2026, 10, 28)}, ExpiryStatus.EXPIRED),
        ({"expiry": TODAY}, ExpiryStatus.EXPIRING_TODAY),
        ({"expiry": date(2026, 11, 5)}, ExpiryStatus.EXPIRING_SOON),
        ({"expiry": date(2026, 11, 6)}, ExpiryStatus.FRESH),
    ],
)
def test_status_precedence(kwargs, expected):
    assert status(**kwargs) == expected


def test_soon_window_uses_largest_positive_interval():
    assert soon_window([7, 3, 1, 0]) == 7
    assert soon_window([14, 2]) == 14
    assert soon_window([0]) == 7
    assert soon_window(None) == 7


@pytest.mark.parametrize(
    ("days_left", "expected"),
    [(10, None), (7, 7), (5, 7), (3, 3), (2, 3), (1, 1), (0, 0), (-1, None)],
)
def test_due_reminder_offset_picks_tightest_due_interval(days_left, expected):
    expiry = date.fromordinal(TODAY.toordinal() + days_left)
    assert due_reminder_offset(expiry, TODAY, [7, 3, 1, 0]) == expected


def test_business_today_uses_timezone():
    # 20:00 UTC on 29 Oct is already 30 Oct in Kolkata but still 29 Oct in New York.
    now = datetime(2026, 10, 29, 20, 0, tzinfo=timezone.utc)
    assert business_today("Asia/Kolkata", now) == date(2026, 10, 30)
    assert business_today("America/New_York", now) == date(2026, 10, 29)
