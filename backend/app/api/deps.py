"""Shared FastAPI dependencies."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.security import read_session
from app.db.session import session_scope
from app.integrations.github.client import GitHubClient
from app.models.user import User
from app.repositories import user_repo
from app.services import auth as auth_service

DbSession = Annotated[Session, Depends(session_scope)]
AppSettings = Annotated[Settings, Depends(get_settings)]


class NotAuthenticatedError(AppError):
    status_code = 401
    code = "not_authenticated"


def _session_token(authorization: str | None) -> str | None:
    """Extract the session from an ``Authorization: Bearer`` header.

    The Next.js server holds the cookie and forwards it as a bearer token, so
    the API itself stays stateless and cookie-domain agnostic.
    """
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    return value


def get_optional_user(
    session: DbSession,
    settings: AppSettings,
    authorization: Annotated[str | None, Header()] = None,
) -> User | None:
    """Resolve the signed-in user, or None. Never raises for anonymity."""
    token = _session_token(authorization)
    if token is None:
        return None

    user_id = read_session(settings, token)
    if user_id is None:
        return None

    try:
        parsed_id = uuid.UUID(user_id)
    except ValueError:
        return None

    return user_repo.get_by_id(session, parsed_id)


def get_current_user(
    user: Annotated[User | None, Depends(get_optional_user)],
) -> User:
    if user is None:
        raise NotAuthenticatedError("Sign in with GitHub to continue.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]


def get_github_client(
    user: CurrentUser,
    settings: AppSettings,
) -> Iterator[GitHubClient]:
    """A GitHub client carrying the signed-in user's token.

    The token may be absent — after a disconnect, or a SECRET_KEY rotation — in
    which case the client is unauthenticated. That still works for public
    repositories, at GitHub's lower anonymous rate limit, so read-only
    operations degrade rather than fail outright.
    """
    token = auth_service.get_github_token(settings, user)
    client = GitHubClient(
        token=token,
        base_url=settings.github_api_url,
        timeout_seconds=settings.github_timeout_seconds,
        max_concurrency=settings.github_max_concurrency,
    )
    try:
        yield client
    finally:
        client.close()


GitHub = Annotated[GitHubClient, Depends(get_github_client)]
