from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.notification import Notification
from app.services import notification_service as ns
from app.services.email_service import EmailNotConfiguredError, PermanentEmailError, TemporaryEmailError
from tests.conftest import FakeSender, create_batch

# 06:30 UTC = 12:00 in Asia/Kolkata (after the default 08:00 send hour).
NOW = datetime.now(timezone.utc).replace(hour=6, minute=30, second=0, microsecond=0)
TODAY = NOW.date()


def schedule(now=NOW) -> int:
    with SessionLocal() as db:
        created = ns.schedule_reminders(db, now)
        db.commit()
        return created


def all_notifications(db):
    db.expire_all()
    return db.scalars(select(Notification).order_by(Notification.created_at)).all()


def test_only_confirmed_active_dates_create_reminders_without_duplicates(client, auth, db):
    create_batch(client, auth, name="Milk", expiry_date=str(TODAY + timedelta(days=3)))
    create_batch(client, auth, name="Cream", expiry_date=str(TODAY + timedelta(days=1)), expiry_confirmed=False)
    create_batch(client, auth, name="Sugar")
    create_batch(client, auth, name="Bread", expiry_date=str(TODAY + timedelta(days=20)))
    empty = create_batch(client, auth, name="Butter", expiry_date=str(TODAY + timedelta(days=1)))
    client.post(
        f"/api/inventory/batches/{empty['id']}/adjust-stock",
        json={"movement_type": "consumed", "quantity": 10},
        headers=auth,
    )

    assert schedule() == 1
    assert schedule() == 0  # idempotent: rerunning (or a restart) creates nothing new
    [notification] = all_notifications(db)
    assert notification.offset_days == 3 and notification.recipient_email == "owner@example.com"


def test_reminder_intervals_progress_and_expired_alert(client, auth, db):
    create_batch(client, auth, expiry_date=str(TODAY + timedelta(days=7)))
    for day in range(0, 9):
        schedule(NOW + timedelta(days=day))
    offsets = [n.offset_days for n in all_notifications(db)]
    assert offsets == [7, 3, 1, 0, -1]


def test_send_hour_and_recipients_respected(client, auth, db):
    create_batch(client, auth, expiry_date=str(TODAY + timedelta(days=1)))
    early = NOW.replace(hour=1)  # 06:30 in Kolkata, before 08:00
    assert schedule(early) == 0
    client.put(
        "/api/settings/notifications",
        json={
            "enabled": True,
            "recipient_emails": ["chef@example.com", "owner@example.com"],
            "reminder_days": [3, 1],
            "notify_on_expired": True,
            "send_hour_local": 8,
        },
        headers=auth,
    )
    assert schedule() == 2
    assert {n.recipient_email for n in all_notifications(db)} == {"chef@example.com", "owner@example.com"}


def test_dispatch_sends_and_marks_sent(client, auth, db):
    batch = create_batch(
        client, auth, name="Milk 500ml", batch_number="A123", expiry_date=str(TODAY + timedelta(days=3))
    )
    schedule()
    sender = FakeSender()
    assert ns.dispatch_pending(SessionLocal, NOW, sender) == {"sent": 1}
    [email] = sender.sent
    assert email.subject == "SmartShelf AI — Milk 500ml expires in 3 days"
    assert "A123" in email.html and "10 packet" in email.text and batch["id"] in email.text
    [notification] = all_notifications(db)
    assert notification.status == "sent" and notification.sent_at is not None
    assert ns.dispatch_pending(SessionLocal, NOW, sender) == {}  # not sent twice


def test_temporary_failures_retry_with_backoff_then_fail(client, auth, db, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "email_max_attempts", 2)
    create_batch(client, auth, expiry_date=str(TODAY + timedelta(days=1)))
    schedule()
    sender = FakeSender(TemporaryEmailError("Could not reach the mail server"))
    assert ns.dispatch_pending(SessionLocal, NOW, sender) == {"pending": 1}
    [notification] = all_notifications(db)
    assert notification.attempts == 1 and notification.next_attempt_at > NOW
    assert ns.dispatch_pending(SessionLocal, NOW, sender) == {}  # backoff not elapsed
    assert ns.dispatch_pending(SessionLocal, NOW + timedelta(minutes=2), sender) == {"failed": 1}
    [notification] = all_notifications(db)
    assert notification.status == "failed" and "mail server" in notification.last_error

    # Manual retry through the API queues it again and dispatches it.
    ok = FakeSender()
    monkeypatch.setattr(ns, "get_email_sender", lambda: ok)
    response = client.post(f"/api/notifications/{notification.id}/retry", headers=auth)
    assert response.status_code == 200
    [notification] = all_notifications(db)
    assert notification.status == "sent" and len(ok.sent) == 1


def test_missing_smtp_or_permanent_error_marks_failed(client, auth, db, monkeypatch):
    create_batch(client, auth, expiry_date=str(TODAY + timedelta(days=1)))
    schedule()
    assert ns.dispatch_pending(SessionLocal, NOW, FakeSender(EmailNotConfiguredError("SMTP is not configured"))) == {
        "failed": 1
    }
    [notification] = all_notifications(db)
    assert notification.last_error == "SMTP is not configured"
    listing = client.get("/api/notifications", params={"status": "failed"}, headers=auth).json()
    assert listing["total"] == 1 and listing["items"][0]["product_name"] == "Milk 500ml"
    monkeypatch.setattr(
        ns, "get_email_sender", lambda: FakeSender(PermanentEmailError("Recipient address was rejected"))
    )
    client.post(f"/api/notifications/{notification.id}/retry", headers=auth)
    [notification] = all_notifications(db)
    assert notification.status == "failed" and "rejected" in notification.last_error and notification.attempts == 1


def test_sent_notification_needs_force_to_resend(client, auth, db):
    create_batch(client, auth, expiry_date=str(TODAY + timedelta(days=1)))
    schedule()
    ns.dispatch_pending(SessionLocal, NOW, FakeSender())
    [notification] = all_notifications(db)
    assert client.post(f"/api/notifications/{notification.id}/retry", headers=auth).status_code == 409
    assert (
        client.post(f"/api/notifications/{notification.id}/retry", params={"force": True}, headers=auth).status_code
        == 200
    )


def test_changing_expiry_cancels_pending_and_stale_notifications(client, auth, db):
    batch = create_batch(client, auth, expiry_date=str(TODAY + timedelta(days=1)))
    schedule()
    client.patch(
        f"/api/inventory/batches/{batch['id']}", json={"expiry_date": str(TODAY + timedelta(days=30))}, headers=auth
    )
    [notification] = all_notifications(db)
    assert notification.status == "cancelled"
    assert schedule() == 0  # new date is outside every interval


def test_settings_validation(client, auth):
    bad = client.put(
        "/api/settings/notifications", json={"recipient_emails": ["not-an-email"], "reminder_days": [3]}, headers=auth
    )
    assert bad.status_code == 422
    neg = client.put("/api/settings/notifications", json={"recipient_emails": [], "reminder_days": [-2]}, headers=auth)
    assert neg.status_code == 422
    ok = client.put(
        "/api/settings/notifications",
        json={"recipient_emails": ["A@Example.com"], "reminder_days": [1, 14, 1]},
        headers=auth,
    ).json()
    assert ok["reminder_days"] == [14, 1] and ok["recipient_emails"] == ["a@example.com"]
    business = client.put(
        "/api/settings/business",
        json={"name": "New Name", "timezone": "Mars/Base", "currency": "INR", "date_format": "DMY"},
        headers=auth,
    )
    assert business.status_code == 422


def test_dashboard_soon_window_follows_settings(client, auth):
    create_batch(client, auth, expiry_date=str(date.today() + timedelta(days=10)))
    assert client.get("/api/dashboard/summary", headers=auth).json()["status_counts"]["fresh"] == 1
    client.put("/api/settings/notifications", json={"recipient_emails": [], "reminder_days": [14, 1]}, headers=auth)
    assert client.get("/api/dashboard/summary", headers=auth).json()["status_counts"]["expiring_soon"] == 1
