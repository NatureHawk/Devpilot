"""GitHub sign-in routes.

The API issues a signed session string; the Next.js server stores it as an
HttpOnly cookie and returns it on later requests as a bearer token. Nothing here
sets a cookie itself, which keeps the API free of cookie-domain assumptions.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.api.deps import AppSettings, CurrentUser, DbSession, OptionalUser
from app.core.errors import ErrorResponse
from app.core.security import issue_session
from app.schemas.auth import (
    AuthorizeUrlResponse,
    CallbackRequest,
    SessionResponse,
    UserRead,
)
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get(
    "/github/authorize",
    response_model=AuthorizeUrlResponse,
    summary="Begin GitHub sign-in",
    responses={501: {"model": ErrorResponse, "description": "GitHub sign-in is not configured"}},
)
def github_authorize(
    settings: AppSettings,
    redirect_path: str = Query(default="/", max_length=500),
) -> AuthorizeUrlResponse:
    return AuthorizeUrlResponse(
        authorize_url=auth_service.start_login(settings, redirect_path=redirect_path)
    )


@router.post(
    "/github/callback",
    response_model=SessionResponse,
    summary="Complete GitHub sign-in",
    responses={401: {"model": ErrorResponse, "description": "The callback could not be verified"}},
)
def github_callback(
    session: DbSession, settings: AppSettings, payload: CallbackRequest
) -> SessionResponse:
    user, redirect_path = auth_service.complete_login(
        session, settings, code=payload.code, state=payload.state
    )
    return SessionResponse(
        session_token=issue_session(settings, str(user.id)),
        redirect_path=redirect_path,
        user=UserRead.model_validate(user),
    )


@router.get(
    "/me",
    response_model=UserRead | None,
    summary="The signed-in account, or null",
)
def read_me(user: OptionalUser) -> UserRead | None:
    """Null rather than 401 for anonymity.

    The frontend renders a signed-out shell for every page, so "nobody is signed
    in" is an ordinary answer here, not an error.
    """
    return UserRead.model_validate(user) if user is not None else None


@router.post(
    "/github/disconnect",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Forget the stored GitHub token",
)
def disconnect_github(session: DbSession, user: CurrentUser) -> None:
    auth_service.disconnect_github(session, user)
