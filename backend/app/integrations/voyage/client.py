"""Voyage AI embedding client.

Implements :class:`app.services.embeddings.provider.EmbeddingProvider` against
Voyage's ``/v1/embeddings`` endpoint. Synchronous, matching the rest of the
backend, and owning its own batching, retries and validation so callers deal in
vectors and typed errors rather than HTTP.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator, Sequence
from types import TracebackType
from typing import Any

import httpx

from app.services.embeddings.provider import (
    EmbeddingError,
    EmbeddingRateLimitError,
    EmbeddingResponseError,
    EmbeddingResult,
    EmbeddingUnauthorizedError,
    InputKind,
)

logger = logging.getLogger(__name__)

_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 1.0

# Voyage caps a request by input count and by total tokens. Tokens are not known
# without the provider's tokenizer, so the payload is bounded by characters
# instead — deliberately conservative, since exceeding the limit fails the whole
# batch and re-running it costs more than sending an extra request.
MAX_BATCH_CHARS = 240_000
# Per-input ceiling. voyage-code-3 accepts 32k tokens; a chunk this long would
# be an indexing bug, and truncating here keeps one oversized input from
# failing an entire batch.
MAX_INPUT_CHARS = 60_000


class VoyageEmbeddingProvider:
    """Hosted embedding provider backed by Voyage AI."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "voyage-code-3",
        dimensions: int = 1024,
        batch_size: int = 64,
        api_url: str = "https://api.voyageai.com/v1/embeddings",
        timeout_seconds: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._dimensions = dimensions
        self._batch_size = batch_size
        self._api_url = api_url
        # `transport` is a test seam so the suite exercises real batching,
        # retry and validation code without a network. Unset in production.
        self._client = httpx.Client(
            timeout=timeout_seconds,
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def __enter__(self) -> VoyageEmbeddingProvider:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # ---- public API -------------------------------------------------------

    def embed_text(self, text: str, *, kind: InputKind) -> list[float]:
        return self.embed_texts([text], kind=kind).vectors[0]

    def embed_texts(self, texts: Sequence[str], *, kind: InputKind) -> EmbeddingResult:
        """Embed many texts, preserving input order across every batch.

        Batches are sent sequentially and their results concatenated, so
        ``vectors[i]`` always corresponds to ``texts[i]``.
        """
        if not texts:
            return EmbeddingResult(vectors=[], model=self._model, dimensions=self._dimensions)

        vectors: list[list[float]] = []
        for batch in self._batches(texts):
            vectors.extend(self._embed_batch(batch, kind=kind))

        if len(vectors) != len(texts):
            raise EmbeddingResponseError(
                "The embedding provider returned a different number of vectors than inputs.",
                details={"expected": len(texts), "received": len(vectors)},
            )
        return EmbeddingResult(vectors=vectors, model=self._model, dimensions=self._dimensions)

    # ---- batching ---------------------------------------------------------

    def _batches(self, texts: Sequence[str]) -> Iterator[list[str]]:
        """Split inputs by both count and total size.

        Yielding lazily keeps only one batch in memory regardless of how many
        chunks a repository produced.
        """
        batch: list[str] = []
        batch_chars = 0

        for text in texts:
            trimmed = text[:MAX_INPUT_CHARS]
            # Flush before adding when this input would break either bound, so
            # a batch is never emitted over the limit.
            if batch and (
                len(batch) >= self._batch_size or batch_chars + len(trimmed) > MAX_BATCH_CHARS
            ):
                yield batch
                batch, batch_chars = [], 0
            batch.append(trimmed)
            batch_chars += len(trimmed)

        if batch:
            yield batch

    # ---- transport --------------------------------------------------------

    def _embed_batch(self, batch: list[str], *, kind: InputKind) -> list[list[float]]:
        payload = {
            "input": batch,
            "model": self._model,
            "input_type": kind.value,
            "output_dimension": self._dimensions,
        }

        response = self._post(payload)
        return self._parse(response, expected=len(batch))

    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        last_error: Exception | None = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = self._client.post(self._api_url, json=payload)
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning("Embedding request failed (attempt %d/%d)", attempt, _MAX_ATTEMPTS)
            else:
                if response.is_success:
                    return response
                if response.status_code in _RETRY_STATUSES and attempt < _MAX_ATTEMPTS:
                    # Exponential backoff; rate limits usually clear quickly.
                    time.sleep(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
                    continue
                self._raise_for_status(response)

            if attempt < _MAX_ATTEMPTS:
                time.sleep(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))

        raise EmbeddingError("The embedding provider could not be reached.") from last_error

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        """Map a failed response to a typed error, without echoing the body.

        Provider errors can quote request content; only the category is useful
        to a caller and only the category is safe to return.
        """
        status_code = response.status_code
        if status_code in (401, 403):
            raise EmbeddingUnauthorizedError("The embedding provider rejected the API key.")
        if status_code == 429:
            raise EmbeddingRateLimitError(
                "The embedding provider's rate limit was reached. Try again shortly."
            )
        logger.warning("Unexpected embedding provider status %s", status_code)
        raise EmbeddingError(
            f"The embedding provider returned an unexpected status ({status_code})."
        )

    def _parse(self, response: httpx.Response, *, expected: int) -> list[list[float]]:
        """Validate a response and return vectors in input order.

        Voyage returns each embedding with its ``index``; sorting by it removes
        any assumption that the provider preserves request order.
        """
        try:
            body = response.json()
        except ValueError as exc:
            raise EmbeddingResponseError(
                "The embedding provider returned a malformed response."
            ) from exc

        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise EmbeddingResponseError("The embedding provider returned an unexpected payload.")

        if len(data) != expected:
            raise EmbeddingResponseError(
                "The embedding provider returned the wrong number of vectors.",
                details={"expected": expected, "received": len(data)},
            )

        try:
            ordered = sorted(data, key=lambda item: int(item["index"]))
            vectors = [[float(value) for value in item["embedding"]] for item in ordered]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingResponseError(
                "The embedding provider returned vectors in an unreadable form."
            ) from exc

        for vector in vectors:
            if len(vector) != self._dimensions:
                # Storing a mis-sized vector would break the pgvector column and
                # silently corrupt similarity search.
                raise EmbeddingResponseError(
                    "The embedding provider returned a vector of unexpected width.",
                    details={"expected": self._dimensions, "received": len(vector)},
                )

        return vectors
