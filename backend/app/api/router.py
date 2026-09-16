"""Versioned API surface.

Routes are mounted under ``/api/v1``; ``/health`` stays unversioned because
orchestrators probe it and should not follow API versioning.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import ask, auth, meta, repositories

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(meta.router)
# Before repositories on purpose: its catch-all `GET /repositories/{owner}/{name}`
# would otherwise capture `GET /repositories/{id}/conversations` and
# `/{id}/changes`. Those routes only match a UUID id, so a repository that is
# genuinely named "changes" still falls through to the repository lookup.
api_router.include_router(ask.router)
api_router.include_router(repositories.router)
