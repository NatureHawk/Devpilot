"""Error taxonomy and the handlers that turn it into HTTP responses.

Every failure leaves the API in the same shape::

    {"error": {"code": "not_found", "message": "...", "details": {...}}}

so the frontend can branch on ``code`` instead of parsing prose. Unexpected
exceptions are logged with a traceback but reported generically — the client
never sees internal detail.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    """Documented in OpenAPI so generated clients know the failure shape."""

    error: ErrorBody


class AppError(Exception):
    """Base class for failures the API raises deliberately."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class IntegrationNotConfiguredError(AppError):
    """Raised when a route needs an integration the deployment has not set up."""

    status_code = status.HTTP_501_NOT_IMPLEMENTED
    code = "integration_not_configured"


class ServiceUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"


def _render(
    status_code: int, code: str, message: str, details: dict[str, Any] | None = None
) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    return JSONResponse(status_code=status_code, content=body.model_dump(exclude_none=True))


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return _render(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _render(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "validation_error",
            "The request payload is invalid.",
            {"errors": exc.errors()},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Covers 404s from unmatched routes and anything raised as HTTPException.
        code = "not_found" if exc.status_code == status.HTTP_404_NOT_FOUND else "http_error"
        return _render(exc.status_code, code, str(exc.detail))

    @app.exception_handler(DBAPIError)
    async def _database_error(request: Request, exc: DBAPIError) -> JSONResponse:
        """A database that cannot answer is unavailability, not a server bug.

        Reported as 503 so clients can retry, and logged in full because the
        driver message can embed the connection string and must not be returned.
        """
        logger.exception("Database error on %s %s", request.method, request.url.path)
        return _render(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            ServiceUnavailableError.code,
            "The database is not reachable.",
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return _render(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "An unexpected error occurred.",
        )
