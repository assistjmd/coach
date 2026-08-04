"""Settings normalisation.

Hosting providers hand out `postgres://` URLs. SQLAlchemy 2 rejects that scheme,
and bare `postgresql://` picks psycopg2, which this project doesn't install — so
a copy-pasted DATABASE_URL fails at import with an error that points at the
driver rather than the URL.
"""

import pytest

from app.config import Settings

BASE = dict(jwt_secret="x", fernet_key="y")


@pytest.mark.parametrize(
    "given",
    [
        "postgres://coach:pw@db.internal:5432/coach",
        "postgresql://coach:pw@db.internal:5432/coach",
        "postgresql+psycopg://coach:pw@db.internal:5432/coach",
    ],
)
def test_database_url_always_selects_psycopg3(given):
    s = Settings(database_url=given, **BASE)
    assert s.database_url == "postgresql+psycopg://coach:pw@db.internal:5432/coach"


def test_credentials_and_query_params_survive_rewriting():
    s = Settings(
        database_url="postgres://u:p%40ss@h:5432/db?sslmode=require", **BASE
    )
    assert s.database_url == "postgresql+psycopg://u:p%40ss@h:5432/db?sslmode=require"


def test_non_postgres_urls_are_left_alone():
    s = Settings(database_url="sqlite:///./local.db", **BASE)
    assert s.database_url == "sqlite:///./local.db"
