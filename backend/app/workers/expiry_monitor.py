"""In-process background scheduler (APScheduler) for the single-instance MVP.

Jobs:
* expiry check   – creates due reminder notifications (idempotent)
* dispatch       – sends pending notifications with bounded retries
* sweep          – re-queues interrupted invoice/scan processing

Each job takes a PostgreSQL advisory lock first, so if several backend
instances are started by accident only one runs a given job at a time. For a
real multi-instance deployment, run the scheduler in one dedicated worker
process (RUN_SCHEDULER=true there, false on API instances) – see README.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import SessionLocal, engine, session_scope
from app.services.notification_service import dispatch_pending, schedule_reminders
from app.services.processing_service import sweep_stuck_jobs

logger = logging.getLogger(__name__)

# Arbitrary, stable advisory lock keys per job.
_LOCK_KEYS = {"expiry_check": 72_001, "dispatch": 72_002, "sweep": 72_003}


def _with_advisory_lock(name: str, job: Callable[[], None]) -> None:
    with engine.connect() as conn:
        acquired = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _LOCK_KEYS[name]}).scalar()
        if not acquired:
            logger.debug("Job %s already running elsewhere; skipping", name)
            return
        try:
            job()
        except Exception:
            logger.exception("Background job %s failed", name)
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEYS[name]})
            conn.commit()


def run_expiry_check() -> None:
    def job() -> None:
        with session_scope() as db:
            schedule_reminders(db)
        dispatch_pending(SessionLocal)

    _with_advisory_lock("expiry_check", job)


def run_dispatch() -> None:
    _with_advisory_lock("dispatch", lambda: dispatch_pending(SessionLocal))


def run_sweep() -> None:
    _with_advisory_lock("sweep", sweep_stuck_jobs)


def create_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    scheduler = BackgroundScheduler(
        timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300}
    )
    # Run shortly after start-up too, so restarts catch up on missed checks.
    from datetime import datetime, timedelta, timezone

    soon = datetime.now(timezone.utc) + timedelta(seconds=10)
    scheduler.add_job(
        run_expiry_check,
        "interval",
        minutes=settings.expiry_check_interval_minutes,
        id="expiry_check",
        next_run_time=soon,
    )
    scheduler.add_job(run_dispatch, "interval", seconds=settings.notification_dispatch_interval_seconds, id="dispatch")
    scheduler.add_job(
        run_sweep, "interval", seconds=settings.processing_sweep_interval_seconds, id="sweep", next_run_time=soon
    )
    return scheduler
