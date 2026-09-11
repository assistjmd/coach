"""Test fixtures.

Environment is set at import time, before anything imports `app.config` — its
`settings()` is `lru_cache`d, so the first read wins. `app.db` builds its engine
lazily for the same reason.

Tests run against a real Postgres (`coach_test`). SQLite is not an option here:
the models use `postgresql.UUID`/`JSONB`, and the sync path uses
`on_conflict_do_nothing(constraint=...)`, which is Postgres-specific.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://coach:coach@localhost:5432/coach_test")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("JWT_SECRET", "test-secret-not-used-anywhere-real")
os.environ.setdefault("FERNET_KEY", "1YMBHLOgqjNq1tCLmDXwWWvKQPMD_ELkZoY6fZ5S-9Y=")

import uuid
from datetime import date

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from alembic import command
from app.auth.jwt import issue_jwt
from app.db import Base, engine, get_db
from app.main import app
from app.models import User, LegalConsent
from app.consent import CURRENT_LEGAL_VERSION


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """Build the test schema once per run, from the migrations themselves.

    Using alembic rather than `create_all` means a migration that doesn't apply
    cleanly fails the suite — which is the point of having migrations.
    """
    if engine().url.database not in {"coach_test", "coach_audit"}:
        raise RuntimeError("Refusing to reset a database other than coach_test or coach_audit")
    Base.metadata.drop_all(bind=engine())
    with engine().begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    command.upgrade(Config("alembic.ini"), "head")
    yield


@pytest.fixture
def db(_schema) -> Session:
    """A session whose writes are discarded after each test.

    `join_transaction_mode="create_savepoint"` lets application code call
    `commit()` normally — those become savepoint releases inside the outer
    transaction, which the fixture then rolls back.
    """
    conn = engine().connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        conn.close()


@pytest.fixture
def client(db) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def unconsented_user(db) -> User:
    u = User(email=f"{uuid.uuid4().hex}@example.com", display_name="Test User")
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture
def user(db, unconsented_user) -> User:
    u = unconsented_user
    u.date_of_birth = date(1990, 1, 1)
    db.add_all([LegalConsent(user_id=u.id, document=doc, version=CURRENT_LEGAL_VERSION)
                for doc in ("terms", "privacy")])
    db.commit()
    return u


@pytest.fixture
def auth(user) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_jwt(user.id)}"}
