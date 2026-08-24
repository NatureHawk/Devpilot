"""Deployment metadata the frontend needs in order to render honest states."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AppSettings
from app.schemas.meta import IntegrationsResponse
from app.services.integrations import describe_integrations

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get(
    "/integrations",
    response_model=IntegrationsResponse,
    summary="Which integrations are configured",
)
def integrations(settings: AppSettings) -> IntegrationsResponse:
    return describe_integrations(settings)
