"""Gemini embedding client: request construction, batching, parsing, validation,
normalisation and rate-limit retry.

Driven through a stub transport so the real batching, retry and parsing code
runs without a network. Nothing here contacts Google.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable

import httpx
import pytest

from app.integrations.gemini import GeminiEmbeddingProvider
from app.services.embeddings.provider import (
    EmbeddingError,
    EmbeddingRateLimitError,
    EmbeddingResponseError,
    EmbeddingUnauthorizedError,
    InputKind,
)

DIMENSIONS = 4


def _raw_vector(seed: float) -> list[float]:
    # Direction varies with `seed` (so ordering across batches is checkable) and
    # the magnitude is deliberately non-unit (so the normalisation step shows).
    return [2.0, seed, 0.0, 0.0]


def _ok(request: httpx.Request) -> httpx.Response:
    """One embedding per request item, in request order."""
    payload = json.loads(request.content)
    count = len(payload["requests"])
    return httpx.Response(
        200,
        json={"embeddings": [{"values": _raw_vector(float(i))} for i in range(count)]},
    )


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    batch_size: int = 100,
    max_retry_seconds: float = 5.0,
    dimensions: int = DIMENSIONS,
) -> GeminiEmbeddingProvider:
    return GeminiEmbeddingProvider(
        api_key="test-key",
        model="gemini-embedding-001",
        dimensions=dimensions,
        batch_size=batch_size,
        max_retry_seconds=max_retry_seconds,
        transport=httpx.MockTransport(handler),
    )


class TestRequestConstruction:
    def test_targets_batch_endpoint_with_qualified_model_and_api_key(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["api_key"] = request.headers.get("x-goog-api-key")
            seen["body"] = json.loads(request.content)
            return _ok(request)

        _provider(handler).embed_texts(["alpha"], kind=InputKind.DOCUMENT)

        assert str(seen["url"]).endswith(
            "/models/gemini-embedding-001:batchEmbedContents"
        )
        assert seen["api_key"] == "test-key"
        body = seen["body"]
        assert isinstance(body, dict)
        first = body["requests"][0]
        assert first["model"] == "models/gemini-embedding-001"
        assert first["content"] == {"parts": [{"text": "alpha"}]}
        assert first["outputDimensionality"] == DIMENSIONS

    def test_task_type_follows_input_kind(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            seen.extend(item["taskType"] for item in payload["requests"])
            return _ok(request)

        provider = _provider(handler)
        provider.embed_texts(["a"], kind=InputKind.DOCUMENT)
        provider.embed_texts(["b"], kind=InputKind.QUERY)

        assert seen == ["RETRIEVAL_DOCUMENT", "CODE_RETRIEVAL_QUERY"]

    def test_empty_input_makes_no_request(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("no HTTP call expected for empty input")

        result = _provider(handler).embed_texts([], kind=InputKind.DOCUMENT)
        assert result.vectors == []
        assert result.provider == "gemini"


class TestBatching:
    def test_splits_by_count_and_preserves_order(self) -> None:
        batches: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            batches.append(len(payload["requests"]))
            return _ok(request)

        texts = [f"t{i}" for i in range(5)]
        result = _provider(handler, batch_size=2).embed_texts(texts, kind=InputKind.DOCUMENT)

        assert batches == [2, 2, 1]
        assert len(result.vectors) == 5
        # Order contract: vectors[i] belongs to texts[i]. Each batch seeds its
        # vectors from position, so concatenation must restart per batch.
        assert result.vectors[0] != result.vectors[1]


class TestResponseParsing:
    def test_returns_unit_normalised_vectors(self) -> None:
        # The raw vector for position 0 is [2, 0, 0, 0]; normalised that is
        # [1, 0, 0, 0] — same direction, unit length.
        result = _provider(_ok).embed_text("a", kind=InputKind.QUERY)

        norm = math.sqrt(sum(component * component for component in result))
        assert norm == pytest.approx(1.0)
        assert result == pytest.approx([1.0, 0.0, 0.0, 0.0])

    def test_normalisation_preserves_direction(self) -> None:
        # Raw [2, 3, 0, 0] -> divided by sqrt(13); the ratio of components is
        # unchanged, which is all cosine similarity depends on.
        provider = _provider(
            lambda _: httpx.Response(200, json={"embeddings": [{"values": [2.0, 3.0, 0.0, 0.0]}]})
        )
        vector = provider.embed_text("a", kind=InputKind.QUERY)

        assert math.sqrt(sum(c * c for c in vector)) == pytest.approx(1.0)
        assert vector[1] / vector[0] == pytest.approx(1.5)

    def test_malformed_body_is_rejected(self) -> None:
        provider = _provider(lambda _: httpx.Response(200, content=b"not json"))
        with pytest.raises(EmbeddingResponseError):
            provider.embed_text("a", kind=InputKind.QUERY)

    def test_missing_values_key_is_rejected(self) -> None:
        provider = _provider(lambda _: httpx.Response(200, json={"embeddings": [{}]}))
        with pytest.raises(EmbeddingResponseError):
            provider.embed_text("a", kind=InputKind.QUERY)

    def test_wrong_vector_count_is_rejected(self) -> None:
        provider = _provider(
            lambda _: httpx.Response(200, json={"embeddings": [{"values": _raw_vector(0.0)}]})
        )
        with pytest.raises(EmbeddingResponseError):
            provider.embed_texts(["a", "b"], kind=InputKind.DOCUMENT)


class TestDimensionValidation:
    def test_wrong_dimension_is_rejected_before_storage(self) -> None:
        provider = _provider(
            lambda _: httpx.Response(200, json={"embeddings": [{"values": [1.0, 2.0]}]})
        )
        with pytest.raises(EmbeddingResponseError) as exc:
            provider.embed_text("a", kind=InputKind.QUERY)
        assert exc.value.details == {"expected": DIMENSIONS, "received": 2}

    def test_requested_output_dimensionality_matches_configuration(self) -> None:
        seen: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            seen.extend(item["outputDimensionality"] for item in payload["requests"])
            return httpx.Response(
                200, json={"embeddings": [{"values": [0.1] * 1024}]}
            )

        _provider(handler, dimensions=1024).embed_text("a", kind=InputKind.DOCUMENT)
        assert seen == [1024]


class TestRateLimitAndRetry:
    def test_succeeds_after_the_rate_limit_clears(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.integrations.gemini.client.time.sleep", lambda _: None)
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
        monkeypatch.setattr("app.integrations.gemini.client.time.sleep", sleeps.append)
        attempts = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            if attempts["count"] == 1:
                return httpx.Response(429, headers={"Retry-After": "7"})
            return _ok(request)

        _provider(handler, max_retry_seconds=30.0).embed_texts(["a"], kind=InputKind.DOCUMENT)
        assert sleeps == [7.0]

    def test_retry_info_delay_in_body_is_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr("app.integrations.gemini.client.time.sleep", sleeps.append)
        attempts = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            if attempts["count"] == 1:
                return httpx.Response(
                    429,
                    json={
                        "error": {
                            "code": 429,
                            "status": "RESOURCE_EXHAUSTED",
                            "details": [
                                {
                                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                                    "retryDelay": "13s",
                                }
                            ],
                        }
                    },
                )
            return _ok(request)

        _provider(handler, max_retry_seconds=60.0).embed_texts(["a"], kind=InputKind.DOCUMENT)
        assert sleeps == [13.0]

    def test_backoff_without_a_hint_grows_and_is_jittered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr("app.integrations.gemini.client.time.sleep", sleeps.append)
        monkeypatch.setattr("app.integrations.gemini.client.random.uniform", lambda a, b: 0.0)
        attempts = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            if attempts["count"] <= 3:
                return httpx.Response(503)
            return _ok(request)

        _provider(handler).embed_texts(["a"], kind=InputKind.DOCUMENT)
        assert sleeps == [1.0, 2.0, 4.0]

    def test_gives_up_after_the_time_budget_and_never_loops_forever(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.integrations.gemini.client.time.sleep", lambda _: None)
        attempts = {"count": 0}

        def handler(_: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            return httpx.Response(429)

        with pytest.raises(EmbeddingRateLimitError) as exc:
            _provider(handler, max_retry_seconds=0.05).embed_text("a", kind=InputKind.QUERY)
        assert exc.value.code == "embedding_rate_limited"
        assert attempts["count"] >= 1

    def test_transport_failure_after_budget_is_a_typed_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.integrations.gemini.client.time.sleep", lambda _: None)

        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        with pytest.raises(EmbeddingError):
            _provider(handler, max_retry_seconds=0.05).embed_text("a", kind=InputKind.QUERY)


class TestErrorMapping:
    def test_bad_key_surfaces_as_unauthorized(self) -> None:
        with pytest.raises(EmbeddingUnauthorizedError):
            _provider(lambda _: httpx.Response(403)).embed_text("a", kind=InputKind.QUERY)

    def test_bad_request_surfaces_as_response_error(self) -> None:
        with pytest.raises(EmbeddingResponseError):
            _provider(lambda _: httpx.Response(400)).embed_text("a", kind=InputKind.QUERY)

    def test_error_message_never_echoes_provider_body(self) -> None:
        provider = _provider(
            lambda _: httpx.Response(403, json={"error": {"message": "key AIza-secret bad"}})
        )
        with pytest.raises(EmbeddingUnauthorizedError) as exc:
            provider.embed_text("a", kind=InputKind.QUERY)
        assert "AIza-secret" not in str(exc.value)
