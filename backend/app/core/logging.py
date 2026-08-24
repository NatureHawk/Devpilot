"""Logging setup.

One place decides the format so that request logs, error logs and library logs
are consistent. Uvicorn's own handlers are replaced rather than duplicated,
otherwise every line is emitted twice.
"""

from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def configure_logging(level: str = "INFO") -> None:
    resolved = getattr(logging, level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt="%Y-%m-%dT%H:%M:%S"))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(resolved)

    for name in _UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True

    # SQLAlchemy is chatty at INFO and its statements can contain user data.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
