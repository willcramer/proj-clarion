"""SQLAlchemy 2.0 engine factory.

Reads the DSN from POSTGRES_* env vars set in `.env`. Single Engine per
process, lazily constructed on first access.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def build_dsn() -> str:
    """Construct the Postgres URL from env vars."""
    user = os.getenv("POSTGRES_USER", "clarion")
    pw = os.getenv("POSTGRES_PASSWORD", "clarion")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "clarion")
    return f"postgresql+psycopg://{user}:{pw}@{host}:{port}/{db}"


@lru_cache(maxsize=1)
def connect() -> Engine:
    """Return a process-wide Engine. Idempotent."""
    return create_engine(build_dsn(), pool_pre_ping=True, future=True)


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=connect(), expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session: commits on success, rolls back on exception."""
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine_cache() -> None:
    """Drop the cached Engine — useful for tests that swap DSNs."""
    connect.cache_clear()
    _session_factory.cache_clear()
