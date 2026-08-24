"""Engine and session lifecycle.

A single module-level engine owns the connection pool. Sessions are per-request
and always closed; routes declared with ``def`` run in FastAPI's threadpool, so
synchronous SQLAlchemy is safe here and keeps the query path easy to reason
about.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        # Recycle before typical cloud/proxy idle timeouts and verify liveness
        # so a restarted database does not surface as a stale-connection error.
        pool_pre_ping=True,
        pool_recycle=1800,
        future=True,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def session_scope() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session.

    Commit is the caller's responsibility; the rollback here only guarantees no
    partial transaction is returned to the pool after an error.
    """
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
