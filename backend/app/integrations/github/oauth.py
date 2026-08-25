"""GitHub OAuth web application flow.

Only the two calls that are not part of the REST API proper: building the
authorize URL, and exchanging an authorization code for an access token. Both
live here rather than in the client because they target github.com, not
api.github.com, and neither needs an existing token.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

import httpx

from app.integrations.github.errors import GitHubUnauthorizedError, GitHubUnavailableError

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"

# read:user for identity, repo for reading private repository contents. Indexing
# only ever reads; no write scope is requested in this milestone.
DEFAULT_SCOPES = ("read:user", "repo")

_TIMEOUT_SECONDS = 15.0


def build_authorize_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    scopes: tuple[str, ...] = DEFAULT_SCOPES,
) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            # Force the account chooser rather than silently reusing a session.
            "allow_signup": "false",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def exchange_code_for_token(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    token_url: str = ACCESS_TOKEN_URL,
) -> tuple[str, str]:
    """Trade an authorization code for an access token.

    Returns ``(access_token, granted_scopes)``. GitHub answers with HTTP 200 even
    when the exchange fails, putting the failure in an ``error`` field, so the
    body is inspected rather than the status code.

    Neither the code nor the resulting token is ever logged.
    """
    try:
        response = httpx.post(
            token_url,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise GitHubUnavailableError("Could not reach GitHub to complete sign-in.") from exc

    if not response.is_success:
        raise GitHubUnavailableError("GitHub rejected the sign-in exchange.")

    payload = response.json()
    if payload.get("error"):
        # error_description can quote the request; log the code only.
        logger.warning("GitHub OAuth exchange failed: %s", payload.get("error"))
        raise GitHubUnauthorizedError("GitHub could not complete the sign-in.")

    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise GitHubUnauthorizedError("GitHub did not return an access token.")

    return token, str(payload.get("scope", ""))
