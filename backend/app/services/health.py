"""Health checks."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import ServiceUnavailableError

logger = logging.getLogger(__name__)


def check_database(session: Session) -> None:
    """Raise ``ServiceUnavailableError`` unless the database answers a trivial query.

    The driver's message can contain the connection string, so it is logged but
    never returned to the client.
    """
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("Database readiness check failed")
        raise ServiceUnavailableError("The database is not reachable.") from None
