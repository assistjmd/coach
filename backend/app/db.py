from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


# Engine and session factory are built on first use, not at import.
#
# Creating them at import time means `import app.main` requires a reachable
# DATABASE_URL before any code runs, which makes test setup ordering-dependent
# (a conftest cannot point the app at a test database after the module is
# already bound to the dev one).
_engine = None
_session_factory: sessionmaker | None = None


def engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings().database_url, pool_pre_ping=True, future=True)
    return _engine


def _factory() -> sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=engine(), autoflush=False, autocommit=False, future=True
        )
    return _session_factory


def SessionLocal(**kwargs) -> Session:
    return _factory()(**kwargs)


def reset_engine() -> None:
    """Drop the cached engine/factory. For tests that repoint DATABASE_URL."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
