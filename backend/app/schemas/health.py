from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Liveness: the process is up. Deliberately touches no dependency."""

    status: Literal["ok"] = "ok"
    service: str
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    """Readiness: the process can serve traffic, i.e. the database answers."""

    status: Literal["ready"] = "ready"
    database: Literal["ok"] = "ok"
