"""Voyage embedding client: batching, ordering, retries and validation.

Driven through a stub transport so the real batching, retry and parsing code
runs without a network. Nothing here contacts Voyage.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from app.integrations.voyage import VoyageEmbeddingProvider
from app.services.embeddings.provider import (
    EmbeddingError,
    EmbeddingRateLimitError,
    EmbeddingResponseError,
    EmbeddingUnauthorizedError,
    InputKind,
)

DIMENSIONS = 4


def _vector(seed: float) -> list[float]:
    return [seed] * DIMENSIONS


def _ok(request: httpx.Request, *, shuffle: bool = False) -> httpx.Response:
    """Echo one vector per input, seeded by position so order is checkable."""
    payload = json.loads(request.content)
    count = len(payload["input"])
    data = [{"index": i, "embedding": _vector(float(i))} for i in range(count)]
    if shuffle:
        # Providers are not required to answer in request order.
        data.reverse()
    return httpx.Response(200, json={"data": data, "model": payload["model"]})


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    batch_size: int = 64,
    max_retry_seconds: float = 5.0,
) -> VoyageEmbeddingProvider:
    return VoyageEmbeddingProvider(
        api_key="test-key",
        model="voyage-code-3",
        dimensions=DIMENSIONS,
        batch_size=batch_size,
        max_retry_seconds=max_retry_seconds,
        transport=httpx.MockTransport(handler),
    )


class TestRequestConstruction:
    def test_sends_model_dimensions_and_input_kind(self) -> None:
        seen: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return _ok(request)

        _provider(handler).embed_texts(["a", "b"], kind=InputKind.DOCUMENT)

        assert seen[0]["model"] == "voyage-code-3"
        assert seen[0]["output_dimension"] == DIMENSIONS
        assert seen[0]["input_type"] == "document"

    def test_query_and_document_use_different_input_types(self) -> None:
        """Asymmetric embedding: the wrong kind degrades ranking silently."""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content)["input_type"])
            return _ok(request)

        provider = _provider(handler)
        provider.embed_text("question", kind=InputKind.QUERY)
        provider.embed_text("source", kind=InputKind.DOCUMENT)

        assert seen == ["query", "document"]

    def test_authorization_header_carries_the_key(self) -> None:
        seen: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("authorization"))
            return _ok(request)

        _provider(handler).embed_text("x", kind=InputKind.QUERY)
        assert seen == ["Bearer test-key"]


class TestBatching:
    def test_splits_by_batch_size(self) -> None:
        batches: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            batches.append(len(json.loads(request.content)["input"]))
            return _ok(request)

        result = _provider(handler, batch_size=2).embed_texts(
            ["a", "b", "c", "d", "e"], kind=InputKind.DOCUMENT
        )

        assert batches == [2, 2, 1]
        assert len(result.vectors) == 5

    def test_preserves_order_across_batches(self) -> None:
        """vectors[i] must belong to texts[i] — the indexer maps by position."""

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            # Seed from the actual input text so provenance is checkable.
            data = [
                {"index": i, "embedding": _vector(float(text))}
                for i, text in enumerate(payload["input"])
            ]
            return httpx.Response(200, json={"data": data, "model": payload["model"]})

        texts = [str(n) for n in range(7)]
        result = _provider(handler, batch_size=3).embed_texts(texts, kind=InputKind.DOCUMENT)

        assert [vector[0] for vector in result.vectors] == [float(n) for n in range(7)]

    def test_reorders_by_provider_index(self) -> None:
        """A provider may answer out of order; index is authoritative."""
        result = _provider(lambda request: _ok(request, shuffle=True)).embed_texts(
            ["a", "b", "c"], kind=InputKind.DOCUMENT
        )
        assert [vector[0] for vector in result.vectors] == [0.0, 1.0, 2.0]

    def test_empty_input_makes_no_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("no request should be made for empty input")

        assert _provider(handler).embed_texts([], kind=InputKind.DOCUMENT).vectors == []


class TestFailures:
    def test_retries_transient_server_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.integrations.voyage.client.time.sleep", lambda _: None)
        attempts = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            if attempts["count"] < 3:
                return httpx.Response(503)
            return _ok(request)

        result = _provider(handler).embed_texts(["a"], kind=InputKind.DOCUMENT)

        assert attempts["count"] == 3
        assert len(result.vectors) == 1

    def test_rate_limit_surfaces_as_typed_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.integrations.voyage.client.time.sleep", lambda _: None)
        provider = _provider(lambda _: httpx.Response(429), max_retry_seconds=0.05)

        with pytest.raises(EmbeddingRateLimitError) as exc:
            provider.embed_text("a", kind=InputKind.QUERY)
        assert exc.value.code == "embedding_rate_limited"

    def test_bad_key_surfaces_as_unauthorized(self) -> None:
        provider = _provider(lambda _: httpx.Response(401))

        with pytest.raises(EmbeddingUnauthorizedError):
            provider.embed_text("a", kind=InputKind.QUERY)

    def test_error_message_never_echoes_provider_body(self) -> None:
        """Provider errors can quote the request; only the category is safe."""
        provider = _provider(
            lambda _: httpx.Response(401, json={"detail": "key sk-secret-123 is invalid"})
        )

        with pytest.raises(EmbeddingUnauthorizedError) as exc:
            provider.embed_text("a", kind=InputKind.QUERY)
        assert "sk-secret-123" not in str(exc.value)

    def test_malformed_body_is_rejected(self) -> None:
        provider = _provider(lambda _: httpx.Response(200, content=b"not json"))

        with pytest.raises(EmbeddingResponseError):
            provider.embed_text("a", kind=InputKind.QUERY)

    def test_wrong_vector_count_is_rejected(self) -> None:
        provider = _provider(
            lambda _: httpx.Response(200, json={"data": [{"index": 0, "embedding": _vector(1.0)}]})
        )

        with pytest.raises(EmbeddingResponseError):
            provider.embed_texts(["a", "b"], kind=InputKind.DOCUMENT)

    def test_wrong_dimension_is_rejected(self) -> None:
        """A mis-sized vector would break the pgvector column and corrupt search."""
        provider = _provider(
            lambda _: httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 2.0]}]})
        )

        with pytest.raises(EmbeddingResponseError) as exc:
            provider.embed_text("a", kind=InputKind.QUERY)
        assert exc.value.details == {"expected": DIMENSIONS, "received": 2}


class TestRetryBehaviour:
    """The actual fix: a real rate-limit window is on the order of a minute,
    not a couple of seconds, and the provider's own `Retry-After` is a direct
    instruction that must win over guessing via backoff."""

    def test_succeeds_after_the_rate_limit_clears(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.integrations.voyage.client.time.sleep", lambda _: None)
        attempts = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            if attempts["count"] == 1:
                return httpx.Response(429)
            return _ok(request)

        result = _provider(handler).embed_texts(["a"], kind=InputKind.DOCUMENT)

        assert attempts["count"] == 2
        assert len(result.vectors) == 1

    def test_retry_after_header_is_used_verbatim(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr("app.integrations.voyage.client.time.sleep", sleeps.append)
        attempts = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            if attempts["count"] == 1:
                return httpx.Response(429, headers={"Retry-After": "7"})
            return _ok(request)

        # A generous budget: the point is that 7 is used as given, not clamped
        # by an unrelated, much smaller time budget.
        _provider(handler, max_retry_seconds=30.0).embed_texts(["a"], kind=InputKind.DOCUMENT)

        assert sleeps == [7.0]

    def test_backoff_without_retry_after_grows_and_is_jittered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr("app.integrations.voyage.client.time.sleep", sleeps.append)
        # No jitter, so the doubling pattern is checkable exactly.
        monkeypatch.setattr("app.integrations.voyage.client.random.uniform", lambda a, b: 0.0)
        attempts = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            if attempts["count"] <= 3:
                return httpx.Response(503)
            return _ok(request)

        _provider(handler).embed_texts(["a"], kind=InputKind.DOCUMENT)

        assert sleeps == [1.0, 2.0, 4.0]

    def test_jitter_keeps_delay_within_a_bounded_range_of_the_base(self) -> None:
        """Direct unit test of the formula: many samples, no request loop or
        time budget involved, so it can't be confused with the exhaustion
        behaviour those concerns actually govern."""
        delays = [VoyageEmbeddingProvider._retry_delay(1, None) for _ in range(200)]

        # Base for attempt 1 is 1.0s, jittered by +/- 25%.
        assert all(0.75 <= delay <= 1.25 for delay in delays)
        # Actual jitter, not a constant — otherwise many concurrent retries
        # would all wake on the same tick.
        assert len(set(delays)) > 1

    def test_gives_up_after_the_time_budget_elapses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The ceiling is wall-clock time, not an attempt count — this must
        terminate even though the provider never stops answering 429."""
        monkeypatch.setattr("app.integrations.voyage.client.time.sleep", lambda _: None)
        attempts = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            return httpx.Response(429)

        provider = _provider(handler, max_retry_seconds=0.05)

        with pytest.raises(EmbeddingRateLimitError):
            provider.embed_text("a", kind=InputKind.QUERY)
        assert attempts["count"] > 1

    def test_transient_errors_still_raise_their_own_typed_error_on_exhaustion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.integrations.voyage.client.time.sleep", lambda _: None)
        provider = _provider(lambda _: httpx.Response(502), max_retry_seconds=0.05)

        with pytest.raises(EmbeddingError):
            provider.embed_text("a", kind=InputKind.QUERY)
