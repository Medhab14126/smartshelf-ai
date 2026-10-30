"""Test configuration.

Integration tests run against a real PostgreSQL database (`smartshelf_test`),
created fresh and migrated with Alembic at the start of the session. The LLM
and SMTP transport are mocked; no external service is contacted.
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://smartshelf:change-me-local-only@localhost:5432/smartshelf_test"
)
_UPLOADS = tempfile.mkdtemp(prefix="smartshelf-test-uploads-")
os.environ.update(
    {
        "DATABASE_URL": TEST_DB_URL,
        "ENVIRONMENT": "test",
        "RUN_SCHEDULER": "false",
        "LLM_ENABLED": "false",
        "SMTP_HOST": "",
        "UPLOAD_DIR": _UPLOADS,
        "AUTH_RATE_LIMIT_ATTEMPTS": "1000",
        "UPLOAD_RATE_LIMIT_PER_MINUTE": "1000",
    }
)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def _recreate_database() -> None:
    url = make_url(TEST_DB_URL)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def database():
    _recreate_database()
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    command.upgrade(cfg, "head")
    yield
    from app.db.session import engine

    engine.dispose()
    shutil.rmtree(_UPLOADS, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_tables(database):
    yield
    from app.db.base import Base
    from app.db.session import engine

    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture(autouse=True)
def inline_processing(monkeypatch):
    from app.core.rate_limit import rate_limiter
    from app.services import processing_service

    monkeypatch.setattr(processing_service, "INLINE_PROCESSING", True)
    rate_limiter.reset()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db():
    from app.db.session import SessionLocal

    session = SessionLocal()
    yield session
    session.close()


def register(
    client, email: str = "owner@example.com", business: str = "Corner Cup Cafe", timezone: str = "Asia/Kolkata"
) -> dict:
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "secret123",
            "full_name": "Asha Rao",
            "business_name": business,
            "timezone": timezone,
        },
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def auth(client) -> dict:
    return register(client)


def create_batch(client, headers, **overrides) -> dict:
    payload = {
        "new_product": {"name": overrides.pop("name", "Milk 500ml"), "category": overrides.pop("category", "Dairy")},
        "quantity": 10,
        "unit": "packet",
    }
    payload.update(overrides)
    response = client.post("/api/inventory/batches", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


class FakeSender:
    """Records emails instead of sending them; can simulate failures."""

    def __init__(self, error: Exception | None = None):
        self.sent: list = []
        self.error = error

    def send(self, email) -> None:
        if self.error:
            raise self.error
        self.sent.append(email)
