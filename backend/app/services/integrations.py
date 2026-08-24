"""Reports which optional integrations this deployment has configured."""

from __future__ import annotations

from app.core.config import Settings
from app.schemas.meta import IntegrationsResponse, IntegrationStatus


def describe_integrations(settings: Settings) -> IntegrationsResponse:
    return IntegrationsResponse(
        integrations=[
            IntegrationStatus(
                name="github",
                configured=settings.github_configured,
                description=("Reads repository contents and opens pull requests on your behalf."),
            ),
            IntegrationStatus(
                name="ai_provider",
                configured=settings.ai_provider_configured,
                description=("Generates repository-grounded answers and proposed code changes."),
            ),
        ]
    )
