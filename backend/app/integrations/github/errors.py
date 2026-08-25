"""GitHub failures, expressed in the application's error taxonomy.

These subclass :class:`AppError`, so the existing handler renders them in the
standard envelope and the frontend can branch on ``code`` exactly as it does
for every other failure.
"""

from __future__ import annotations

from fastapi import status

from app.core.errors import AppError


class GitHubError(AppError):
    """Base for anything GitHub-related that stops an operation."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "github_error"


class GitHubUnauthorizedError(GitHubError):
    """The token is missing, expired, or lacks the required scope."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "github_unauthorized"


class GitHubForbiddenError(GitHubError):
    """Authenticated, but not permitted — typically a private repo without scope."""

    status_code = status.HTTP_403_FORBIDDEN
    code = "github_forbidden"


class GitHubRateLimitError(GitHubError):
    """Primary or secondary rate limit reached."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "github_rate_limited"


class GitHubNotFoundError(GitHubError):
    """No such repository, ref, or blob — or it is invisible to this token."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "github_not_found"


class GitHubUnavailableError(GitHubError):
    """GitHub could not be reached, or returned a server error."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "github_unavailable"
