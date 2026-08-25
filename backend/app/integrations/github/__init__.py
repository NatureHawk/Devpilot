"""GitHub integration: REST client, OAuth flow, and typed payloads."""

from app.integrations.github.client import GitHubClient
from app.integrations.github.errors import (
    GitHubError,
    GitHubForbiddenError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubUnauthorizedError,
    GitHubUnavailableError,
)
from app.integrations.github.models import (
    GitHubRepository,
    GitHubUser,
    RepositoryTree,
    TreeEntry,
)

__all__ = [
    "GitHubClient",
    "GitHubError",
    "GitHubForbiddenError",
    "GitHubNotFoundError",
    "GitHubRateLimitError",
    "GitHubRepository",
    "GitHubUnauthorizedError",
    "GitHubUnavailableError",
    "GitHubUser",
    "RepositoryTree",
    "TreeEntry",
]
