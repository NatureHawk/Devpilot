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

# Applied per resolved address, and "localhost" resolves to both ::1 and
# 127.0.0.1 — so the worst case is twice this. Kept at 3s to stay inside the
# frontend's 8s request budget while tolerating a slow local start.
CONNECT_TIMEOUT_SECONDS = 3


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        # Recycle before typical cloud/proxy idle timeouts and verify liveness
        # so a restarted database does not surface as a stale-connection error.
        pool_pre_ping=True,
        pool_recycle=1800,
        # Without this, an unreachable host is left to the OS TCP timeout, and a
        # request hangs for tens of seconds instead of returning a prompt 503.
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
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
