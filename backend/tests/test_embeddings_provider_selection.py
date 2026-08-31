"""Which embedding provider `get_provider` builds, and when a deployment counts
as configured, both follow EMBEDDING_PROVIDER and nothing else.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.integrations.gemini import GeminiEmbeddingProvider
from app.integrations.voyage import VoyageEmbeddingProvider
from app.services.embeddings import get_provider
from app.services.embeddings.provider import EmbeddingNotConfiguredError


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key in (
        "EMBEDDING_PROVIDER",
        "VOYAGE_API_KEY",
        "GEMINI_API_KEY",
        "EMBEDDING_MODEL",
        "GEMINI_EMBEDDING_MODEL",
        "EMBEDDING_DIMENSIONS",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


class TestConfiguredFlag:
    def test_voyage_selected_needs_only_the_voyage_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        s = _settings(monkeypatch, EMBEDDING_PROVIDER="voyage", VOYAGE_API_KEY="v-key")
        assert s.embeddings_configured is True

        s = _settings(monkeypatch, EMBEDDING_PROVIDER="voyage", GEMINI_API_KEY="g-key")
        assert s.embeddings_configured is False

    def test_gemini_selected_needs_only_the_gemini_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        s = _settings(monkeypatch, EMBEDDING_PROVIDER="gemini", GEMINI_API_KEY="g-key")
        assert s.embeddings_configured is True

        s = _settings(monkeypatch, EMBEDDING_PROVIDER="gemini", VOYAGE_API_KEY="v-key")
        assert s.embeddings_configured is False

    def test_default_provider_is_voyage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        s = _settings(monkeypatch, VOYAGE_API_KEY="v-key")
        assert s.embedding_provider == "voyage"

    def test_rejects_an_unknown_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ValueError):
            _settings(monkeypatch, EMBEDDING_PROVIDER="cohere", VOYAGE_API_KEY="v-key")


class TestActiveModel:
    def test_active_model_tracks_the_selected_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        voyage = _settings(monkeypatch, EMBEDDING_PROVIDER="voyage", VOYAGE_API_KEY="v")
        assert voyage.active_embedding_model == "voyage-code-3"

        gemini = _settings(monkeypatch, EMBEDDING_PROVIDER="gemini", GEMINI_API_KEY="g")
        assert gemini.active_embedding_model == "gemini-embedding-001"


class TestGetProvider:
    def test_builds_voyage_when_selected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        s = _settings(monkeypatch, EMBEDDING_PROVIDER="voyage", VOYAGE_API_KEY="v-key")
        provider = get_provider(s)
        assert isinstance(provider, VoyageEmbeddingProvider)
        assert provider.provider == "voyage"
        assert provider.model == "voyage-code-3"

    def test_builds_gemini_when_selected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        s = _settings(monkeypatch, EMBEDDING_PROVIDER="gemini", GEMINI_API_KEY="g-key")
        provider = get_provider(s)
        assert isinstance(provider, GeminiEmbeddingProvider)
        assert provider.provider == "gemini"
        assert provider.model == "gemini-embedding-001"
        assert provider.dimensions == s.embedding_dimensions

    def test_raises_when_the_selected_provider_has_no_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        s = _settings(monkeypatch, EMBEDDING_PROVIDER="gemini", VOYAGE_API_KEY="v-key")
        with pytest.raises(EmbeddingNotConfiguredError):
            get_provider(s)

    def test_both_providers_expose_the_same_dimension_contract(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The stored pgvector width is one number, shared by both providers."""
        voyage = get_provider(
            _settings(monkeypatch, EMBEDDING_PROVIDER="voyage", VOYAGE_API_KEY="v")
        )
        gemini = get_provider(
            _settings(monkeypatch, EMBEDDING_PROVIDER="gemini", GEMINI_API_KEY="g")
        )
        assert voyage.dimensions == gemini.dimensions == 1024
