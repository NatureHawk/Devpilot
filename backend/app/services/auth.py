"""GitHub sign-in.

The browser never holds a GitHub token. It holds a signed session cookie naming
a DevPilot user; the GitHub token is encrypted at rest and only ever leaves the
database to be put in an Authorization header inside this process.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import IntegrationNotConfiguredError
from app.core.security import (
    TokenDecryptionError,
    decrypt_token,
    encrypt_token,
    issue_oauth_state,
    read_oauth_state,
)
from app.integrations.github.client import GitHubClient
from app.integrations.github.errors import GitHubUnauthorizedError
from app.integrations.github.oauth import build_authorize_url, exchange_code_for_token
from app.models.user import User
from app.repositories import user_repo

logger = logging.getLogger(__name__)


def github_callback_url(settings: Settings) -> str:
    """Where GitHub sends the browser back.

    Points at the API, not the frontend: the code must be exchanged using the
    client secret, which only the backend holds.
    """
    return f"{settings.frontend_url.rstrip('/')}/api/auth/github/callback"


def require_github_oauth(settings: Settings) -> None:
    if not settings.github_configured:
        raise IntegrationNotConfiguredError(
            "GitHub sign-in is not configured for this deployment.",
            details={"integration": "github"},
        )


def start_login(settings: Settings, *, redirect_path: str = "/") -> str:
    """Build the URL that begins the OAuth round trip."""
    require_github_oauth(settings)
    state = issue_oauth_state(settings, redirect_path=redirect_path)
    return build_authorize_url(
        client_id=settings.github_client_id,
        redirect_uri=github_callback_url(settings),
        state=state,
    )


def complete_login(
    session: Session, settings: Settings, *, code: str, state: str
) -> tuple[User, str]:
    """Finish the OAuth round trip and return the signed-in user.

    The state is verified before the code is spent: an unverified state means a
    forged or replayed callback, and the code must not be exchanged.
    """
    require_github_oauth(settings)

    redirect_path = read_oauth_state(settings, state)
    if redirect_path is None:
        raise GitHubUnauthorizedError("The sign-in link has expired. Try again.")

    token, scopes = exchange_code_for_token(
        client_id=settings.github_client_id,
        client_secret=settings.github_client_secret,
        code=code,
        redirect_uri=github_callback_url(settings),
    )

    with GitHubClient(
        token=token,
        base_url=settings.github_api_url,
        timeout_seconds=settings.github_timeout_seconds,
    ) as client:
        profile = client.get_authenticated_user()

    user = user_repo.upsert_from_github(
        session,
        github_id=profile.id,
        login=profile.login,
        display_name=profile.name,
        email=profile.email,
        avatar_url=profile.avatar_url,
        token_encrypted=encrypt_token(settings, token),
        token_scopes=scopes,
    )
    session.commit()

    logger.info("GitHub sign-in completed user_id=%s login=%s", user.id, profile.login)
    return user, redirect_path


def disconnect_github(session: Session, user: User) -> None:
    """Forget the stored token without deleting the account."""
    user.github_token_encrypted = None
    user.github_token_scopes = None
    session.commit()


def get_github_token(settings: Settings, user: User) -> str | None:
    """Decrypt a user's GitHub token, or None if there is not a usable one.

    A token that fails to decrypt — after a SECRET_KEY rotation, say — is
    treated as absent rather than raising: the user simply needs to reconnect.
    """
    if not user.github_token_encrypted:
        return None
    try:
        return decrypt_token(settings, user.github_token_encrypted)
    except TokenDecryptionError:
        logger.warning("Stored GitHub token could not be decrypted user_id=%s", user.id)
        return None
