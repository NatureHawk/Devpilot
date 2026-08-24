"""Versioned API surface.

Routes are mounted under ``/api/v1``; ``/health`` stays unversioned because
orchestrators probe it and should not follow API versioning.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import meta, repositories

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(meta.router)
api_router.include_router(repositories.router)
