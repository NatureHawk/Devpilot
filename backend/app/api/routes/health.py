"""Liveness and readiness probes."""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.api.deps import AppSettings, DbSession
from app.core.errors import ErrorResponse
from app.schemas.health import HealthResponse, ReadinessResponse
from app.services.health import check_database

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
def health(settings: AppSettings) -> HealthResponse:
    return HealthResponse(
        service="devpilot-api", version=__version__, environment=settings.environment
    )


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={503: {"model": ErrorResponse, "description": "A dependency is unavailable"}},
)
def readiness(session: DbSession) -> ReadinessResponse:
    check_database(session)
    return ReadinessResponse()
