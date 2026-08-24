from __future__ import annotations

from pydantic import BaseModel, Field


class IntegrationStatus(BaseModel):
    """Whether an integration has credentials configured.

    Only the boolean is exposed — never the credential itself, and never a
    partial/masked value.
    """

    name: str
    configured: bool
    description: str


class IntegrationsResponse(BaseModel):
    integrations: list[IntegrationStatus] = Field(default_factory=list)
