"""Embedding generation.

The provider is resolved here so that swapping Voyage for a local model later
touches this module and nothing else.
"""

from typing import TYPE_CHECKING

from app.services.embeddings.provider import (
    EmbeddingError,
    EmbeddingNotConfiguredError,
    EmbeddingProvider,
    EmbeddingRateLimitError,
    EmbeddingResponseError,
    EmbeddingResult,
    EmbeddingUnauthorizedError,
    InputKind,
)
from app.services.embeddings.text import build_embedding_text

if TYPE_CHECKING:
    from app.core.config import Settings

__all__ = [
    "EmbeddingError",
    "EmbeddingNotConfiguredError",
    "EmbeddingProvider",
    "EmbeddingRateLimitError",
    "EmbeddingResponseError",
    "EmbeddingResult",
    "EmbeddingUnauthorizedError",
    "InputKind",
    "build_embedding_text",
    "get_provider",
]


def get_provider(settings: "Settings") -> EmbeddingProvider:
    """Build the configured embedding provider.

    Raises :class:`EmbeddingNotConfiguredError` rather than constructing a
    client that would fail with a provider error on first use.
    """
    from app.integrations.voyage import VoyageEmbeddingProvider

    if not settings.embeddings_configured:
        raise EmbeddingNotConfiguredError(
            "No embedding provider is configured for this deployment."
        )

    return VoyageEmbeddingProvider(
        api_key=settings.voyage_api_key,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        batch_size=settings.embedding_batch_size,
        api_url=settings.embedding_api_url,
        timeout_seconds=settings.embedding_timeout_seconds,
    )
