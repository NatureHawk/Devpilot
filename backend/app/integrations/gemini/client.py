"""Google Gemini embedding client.

Implements :class:`app.services.embeddings.provider.EmbeddingProvider` against
the Gemini API's ``:batchEmbedContents`` endpoint. Synchronous and SDK-free,
matching the Voyage and OpenRouter integrations: plain ``httpx``, with batching,
retries and validation owned here so callers deal in vectors and typed errors
rather than HTTP.

Model: ``gemini-embedding-001``. Its native width is 3072; it is a Matryoshka
(MRL) model, so ``outputDimensionality`` returns a genuine embedding at the
requested width rather than a truncation of the 3072-d one. We request the
configured ``EMBEDDING_DIMENSIONS`` (1024 by default) so the existing
``vector(1024)`` schema is untouched. gemini-embedding-001 does not L2-normalise
sub-3072 outputs, and Google's own guidance is to normalise them, so this client
does — a direction-preserving rescale that leaves cosine ranking identical and
never changes the dimension.
"""

from __future__ import annotations

import logging
import math
import random
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
# Backoff is time-bounded (see `max_retry_seconds`), not attempt-counted: a
# free-tier rate-limit window can be on the order of a minute. These only shape
# one sleep's length.
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_MAX_SECONDS = 30.0
_JITTER_FRACTION = 0.25

# gemini-embedding-001's input limit is 2048 tokens; ~8k characters is a
# conservative ceiling for code. An input longer than this is an indexing bug,
# and trimming keeps one oversized chunk from failing an entire batch.
MAX_INPUT_CHARS = 8_000
# Whole-request guard for :batchEmbedContents. 100 inputs * 8k chars leaves head
# room under this.
MAX_BATCH_CHARS = 900_000

# DevPilot embeds code chunks as documents and natural-language questions as
# queries. CODE_RETRIEVAL_QUERY is Google's recommended task type for the
# "find code from a natural-language question" direction, paired with
# RETRIEVAL_DOCUMENT for the indexed corpus.
_TASK_TYPE: dict[InputKind, str] = {
    InputKind.DOCUMENT: "RETRIEVAL_DOCUMENT",
    InputKind.QUERY: "CODE_RETRIEVAL_QUERY",
}


def _parse_retry_after(response: httpx.Response | None) -> float | None:
    """A retry delay in seconds from the response, or None.

    The Gemini API sometimes sends a ``Retry-After`` header and sometimes a
    ``RetryInfo`` detail in the JSON error body (``retryDelay: "17s"``); this
    honours whichever is present, header first.
    """
    if response is None:
        return None

    header = response.headers.get("retry-after")
    if header is not None:
        try:
            seconds = float(header)
        except ValueError:
            seconds = -1.0
        if seconds >= 0:
            return seconds

    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    details = (body.get("error") or {}).get("details") or []
    for detail in details:
        if not isinstance(detail, dict):
            continue
        if str(detail.get("@type", "")).endswith("RetryInfo"):
            raw = str(detail.get("retryDelay", "")).strip()
            if raw.endswith("s"):
                raw = raw[:-1]
            try:
                seconds = float(raw)
            except ValueError:
                return None
            return seconds if seconds >= 0 else None
    return None


class GeminiEmbeddingProvider:
    """Hosted embedding provider backed by the Google Gemini API."""

    provider = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-embedding-001",
        dimensions: int = 1024,
        batch_size: int = 100,
        api_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout_seconds: float = 60.0,
        max_retry_seconds: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._dimensions = dimensions
        self._batch_size = batch_size
        self._base_url = api_url.rstrip("/")
        self._max_retry_seconds = max_retry_seconds
        # `transport` is a test seam so the suite exercises real batching, retry
        # and parsing code without a network. Unset in production.
        self._client = httpx.Client(
            timeout=timeout_seconds,
            transport=transport,
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
            },
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def __enter__(self) -> GeminiEmbeddingProvider:
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

        Batches are sent sequentially — bounded concurrency of one — and their
        results concatenated, so ``vectors[i]`` always corresponds to
        ``texts[i]``.
        """
        if not texts:
            return EmbeddingResult(
                vectors=[], model=self._model, dimensions=self._dimensions, provider="gemini"
            )

        vectors: list[list[float]] = []
        for batch in self._batches(texts):
            vectors.extend(self._embed_batch(batch, kind=kind))

        if len(vectors) != len(texts):
            raise EmbeddingResponseError(
                "The embedding provider returned a different number of vectors than inputs.",
                details={"expected": len(texts), "received": len(vectors)},
            )
        return EmbeddingResult(
            vectors=vectors, model=self._model, dimensions=self._dimensions, provider="gemini"
        )

    # ---- batching -------------------------------------------------------

    def _batches(self, texts: Sequence[str]) -> Iterator[list[str]]:
        """Split inputs by both count and total size, lazily."""
        batch: list[str] = []
        batch_chars = 0

        for text in texts:
            trimmed = text[:MAX_INPUT_CHARS]
            if batch and (
                len(batch) >= self._batch_size or batch_chars + len(trimmed) > MAX_BATCH_CHARS
            ):
                yield batch
                batch, batch_chars = [], 0
            batch.append(trimmed)
            batch_chars += len(trimmed)

        if batch:
            yield batch

    # ---- transport -----------------------------------------------------

    def _embed_batch(self, batch: list[str], *, kind: InputKind) -> list[list[float]]:
        qualified_model = f"models/{self._model}"
        task_type = _TASK_TYPE[kind]
        payload = {
            "requests": [
                {
                    "model": qualified_model,
                    "content": {"parts": [{"text": text}]},
                    "taskType": task_type,
                    "outputDimensionality": self._dimensions,
                }
                for text in batch
            ]
        }

        url = f"{self._base_url}/{qualified_model}:batchEmbedContents"
        response = self._post(url, payload)
        return self._parse(response, expected=len(batch))

    def _post(self, url: str, payload: dict[str, Any]) -> httpx.Response:
        """POST with retry, bounded by wall-clock time rather than attempt count.

        A rate-limit response is retried until ``max_retry_seconds`` has elapsed,
        honouring the provider's ``Retry-After`` / ``RetryInfo`` when present and
        falling back to exponential backoff with jitter otherwise. It never
        retries forever: once the budget is spent the typed error is raised.
        """
        start = time.monotonic()
        attempt = 0
        last_error: Exception | None = None
        last_response: httpx.Response | None = None

        while True:
            attempt += 1
            try:
                response = self._client.post(url, json=payload)
            except httpx.HTTPError as exc:
                last_error = exc
                last_response = None
                logger.warning("Embedding request failed (attempt %d)", attempt)
            else:
                if response.is_success:
                    return response
                if response.status_code not in _RETRY_STATUSES:
                    self._raise_for_status(response)
                last_response = response
                last_error = None

            elapsed = time.monotonic() - start
            remaining = self._max_retry_seconds - elapsed
            if remaining <= 0:
                if last_response is not None:
                    self._raise_for_status(last_response)
                raise EmbeddingError(
                    "The embedding provider could not be reached."
                ) from last_error

            delay = min(self._retry_delay(attempt, last_response), remaining)
            logger.info(
                "Retrying embedding request in %.1fs (attempt %d, %.0fs remaining)",
                delay,
                attempt,
                remaining,
            )
            time.sleep(delay)

    @staticmethod
    def _retry_delay(attempt: int, response: httpx.Response | None) -> float:
        """The provider's own retry hint wins outright; otherwise backoff+jitter."""
        hinted = _parse_retry_after(response)
        if hinted is not None:
            return hinted

        exponent = min(attempt - 1, 10)
        base = min(_BACKOFF_MAX_SECONDS, _BACKOFF_BASE_SECONDS * (2**exponent))
        jitter = base * random.uniform(-_JITTER_FRACTION, _JITTER_FRACTION)
        return float(max(0.0, base + jitter))

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        """Map a failed response to a typed error, without echoing the body.

        Provider errors can quote request content — here that includes
        repository source — so only the category is returned.
        """
        status_code = response.status_code
        if status_code in (401, 403):
            raise EmbeddingUnauthorizedError("The embedding provider rejected the API key.")
        if status_code == 429:
            raise EmbeddingRateLimitError(
                "The embedding provider's rate limit was reached. Try again shortly."
            )
        if status_code in (400, 404):
            logger.warning("Gemini rejected the embedding request (%s)", status_code)
            raise EmbeddingResponseError(
                "The embedding provider rejected the request. Check GEMINI_EMBEDDING_MODEL "
                "and EMBEDDING_DIMENSIONS."
            )
        logger.warning("Unexpected embedding provider status %s", status_code)
        raise EmbeddingError(
            f"The embedding provider returned an unexpected status ({status_code})."
        )

    def _parse(self, response: httpx.Response, *, expected: int) -> list[list[float]]:
        """Validate a response and return unit-normalised vectors in input order.

        ``:batchEmbedContents`` returns ``embeddings`` in request order, with no
        per-item index, so order is taken as given — the same positional
        contract the caller relies on.
        """
        try:
            body = response.json()
        except ValueError as exc:
            raise EmbeddingResponseError(
                "The embedding provider returned a malformed response."
            ) from exc

        data = body.get("embeddings") if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise EmbeddingResponseError("The embedding provider returned an unexpected payload.")

        if len(data) != expected:
            raise EmbeddingResponseError(
                "The embedding provider returned the wrong number of vectors.",
                details={"expected": expected, "received": len(data)},
            )

        try:
            vectors = [[float(value) for value in item["values"]] for item in data]
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

        return [_normalise(vector) for vector in vectors]


def _normalise(vector: list[float]) -> list[float]:
    """L2-normalise to unit length.

    gemini-embedding-001 does not normalise sub-3072 outputs and Google's
    guidance is to do it client-side. It preserves direction exactly, so cosine
    ranking is unchanged; it just makes the stored vectors well-formed unit
    vectors. A zero vector (which the model does not produce for real text) is
    returned unchanged rather than divided by zero.
    """
    norm = math.sqrt(sum(component * component for component in vector))
    if norm == 0.0:
        return vector
    return [component / norm for component in vector]
