"""The embedding interface the rest of the application depends on.

Nothing outside this package should know which provider is in use. A local
model can replace the hosted one later by implementing :class:`EmbeddingProvider`
and changing what :func:`app.services.embeddings.get_provider` returns.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from fastapi import status

from app.core.errors import AppError


class InputKind(StrEnum):
    """What an embedding is for.

    Retrieval models embed a question and a piece of source differently — the
    two are asymmetric — so the caller must say which it is. Getting this wrong
    silently degrades ranking rather than raising, which is why it is a required
    argument and not a default.
    """

    DOCUMENT = "document"
    QUERY = "query"


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """Vectors plus the identity of what produced them.

    Model and dimensions travel with the vectors so callers can persist them
    alongside, and so mixing incompatible vectors is detectable rather than a
    silent correctness bug.
    """

    vectors: list[list[float]]
    model: str
    dimensions: int


class EmbeddingError(AppError):
    """Base for embedding failures, in the application's error taxonomy."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "embedding_error"


class EmbeddingNotConfiguredError(EmbeddingError):
    """No credentials for the embedding provider in this deployment."""

    status_code = status.HTTP_501_NOT_IMPLEMENTED
    code = "embedding_not_configured"


class EmbeddingUnauthorizedError(EmbeddingError):
    """The provider rejected the API key."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "embedding_unauthorized"


class EmbeddingRateLimitError(EmbeddingError):
    """The provider's rate or quota limit was reached."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "embedding_rate_limited"


class EmbeddingResponseError(EmbeddingError):
    """The provider replied with something unusable.

    Covers a malformed body and a dimension mismatch: both mean the vectors
    cannot be trusted, and storing them would corrupt the index.
    """

    code = "embedding_invalid_response"


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Turns text into vectors.

    Implementations must preserve order: ``vectors[i]`` belongs to ``texts[i]``.
    That guarantee is what lets the indexer map results back to chunks by
    position rather than by echoing identifiers through the provider.
    """

    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed_texts(self, texts: Sequence[str], *, kind: InputKind) -> EmbeddingResult: ...

    def embed_text(self, text: str, *, kind: InputKind) -> list[float]: ...
