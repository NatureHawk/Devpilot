"""Retrieval: the embedded representation, request validation, and API states."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.deps import get_current_user
from app.api.routes import repositories as repositories_route
from app.db.session import session_scope
from app.models.repository import IndexingStatus, Repository, RepositoryVisibility
from app.models.user import User
from app.schemas.search import SearchRequest
from app.services.embeddings import build_embedding_text


@pytest.fixture
def signed_in(app: FastAPI) -> User:
    """Stand in for a signed-in account without touching the database."""
    user = User(id=uuid.uuid4(), github_id=1, github_login="tester")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[session_scope] = lambda: object()
    yield user
    app.dependency_overrides.clear()


def _repository(user: User, **overrides: object) -> Repository:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "provider": "github",
        "owner": "acme",
        "name": "api",
        "default_branch": "main",
        "visibility": RepositoryVisibility.PUBLIC,
        "indexing_status": IndexingStatus.INDEXED,
        "connected_by_user_id": user.id,
    }
    defaults.update(overrides)
    return Repository(**defaults)


class TestEmbeddingText:
    """The representation is a contract: changing it invalidates every vector."""

    def test_includes_location_and_symbol(self) -> None:
        text = build_embedding_text(
            repository_full_name="acme/api",
            file_path="app/auth/service.py",
            language="python",
            chunk_type="method",
            symbol="authenticate",
            parent_symbol="AuthService",
            content="def authenticate(self): ...",
        )

        assert "Repository: acme/api" in text
        assert "Path: app/auth/service.py" in text
        assert "Language: python" in text
        # A method is far more findable when qualified by its class.
        assert "Symbol: AuthService.authenticate" in text
        assert "def authenticate(self): ..." in text

    def test_is_deterministic(self) -> None:
        """Re-embedding an unchanged chunk must produce identical input."""
        kwargs = {
            "repository_full_name": "acme/api",
            "file_path": "a.py",
            "language": "python",
            "chunk_type": "function",
            "symbol": "f",
            "parent_symbol": None,
            "content": "def f(): ...",
        }
        assert build_embedding_text(**kwargs) == build_embedding_text(**kwargs)

    def test_handles_a_chunk_with_no_symbol(self) -> None:
        text = build_embedding_text(
            repository_full_name="acme/api",
            file_path="README.md",
            language="markdown",
            chunk_type="block",
            symbol=None,
            parent_symbol=None,
            content="# Title",
        )
        assert "Symbol:" not in text
        assert "# Title" in text

    def test_is_bounded(self) -> None:
        text = build_embedding_text(
            repository_full_name="acme/api",
            file_path="big.py",
            language="python",
            chunk_type="function",
            symbol="f",
            parent_symbol=None,
            content="x" * 100_000,
        )
        assert len(text) <= 32_000


class TestSearchRequestValidation:
    def test_rejects_an_empty_query(self) -> None:
        with pytest.raises(ValidationError):
            SearchRequest(query="")

    def test_rejects_a_whitespace_only_query(self) -> None:
        """Passes min_length but carries no meaning."""
        with pytest.raises(ValidationError):
            SearchRequest(query="   \n\t ")

    def test_trims_surrounding_whitespace(self) -> None:
        assert SearchRequest(query="  auth  ").query == "auth"

    def test_top_k_defaults_to_the_configured_source_cap(self) -> None:
        """Unset means CONTEXT_MAX_SOURCES decides, not a hardcoded eight."""
        assert SearchRequest(query="auth").top_k is None

    @pytest.mark.parametrize("top_k", [0, -1, 51, 5000])
    def test_rejects_out_of_range_top_k(self, top_k: int) -> None:
        with pytest.raises(ValidationError):
            SearchRequest(query="auth", top_k=top_k)

    @pytest.mark.parametrize("top_k", [1, 8, 50])
    def test_accepts_bounded_top_k(self, top_k: int) -> None:
        assert SearchRequest(query="auth", top_k=top_k).top_k == top_k


class TestSearchEndpoint:
    def test_requires_authentication(self, client: TestClient) -> None:
        response = client.post(
            f"/api/v1/repositories/{uuid.uuid4()}/search", json={"query": "auth"}
        )

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "not_authenticated"

    def test_rejects_an_empty_query_before_touching_the_provider(
        self, client: TestClient, signed_in: User
    ) -> None:
        response = client.post(f"/api/v1/repositories/{uuid.uuid4()}/search", json={"query": "  "})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    def test_rejects_out_of_range_top_k(self, client: TestClient, signed_in: User) -> None:
        response = client.post(
            f"/api/v1/repositories/{uuid.uuid4()}/search", json={"query": "auth", "top_k": 500}
        )

        assert response.status_code == 422

    def test_repository_owned_by_someone_else_is_not_found(
        self, client: TestClient, signed_in: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ownership failures read as missing, never as forbidden."""
        other = _repository(User(id=uuid.uuid4()))
        monkeypatch.setattr(repositories_route.repository_repo, "get_by_id", lambda *a, **k: other)

        response = client.post(f"/api/v1/repositories/{other.id}/search", json={"query": "auth"})

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_unindexed_repository_reports_why(
        self, client: TestClient, signed_in: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repository = _repository(signed_in, indexing_status=IndexingStatus.NOT_INDEXED)
        monkeypatch.setattr(
            repositories_route.repository_repo, "get_by_id", lambda *a, **k: repository
        )

        response = client.post(
            f"/api/v1/repositories/{repository.id}/search", json={"query": "auth"}
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "repository_not_indexed"

    def test_indexed_repository_without_vectors_is_not_an_empty_result(
        self, client: TestClient, signed_in: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No vectors is a different answer from no matches, and must say so."""
        repository = _repository(signed_in, embedding_model="voyage-code-3")
        monkeypatch.setattr(
            repositories_route.repository_repo, "get_by_id", lambda *a, **k: repository
        )
        monkeypatch.setattr(
            "app.services.retrieval.embedding_repo.count_embeddings", lambda *a, **k: 0
        )

        response = client.post(
            f"/api/v1/repositories/{repository.id}/search", json={"query": "auth"}
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "repository_not_searchable"

    def test_unconfigured_provider_reports_configuration_not_a_crash(
        self, client: TestClient, signed_in: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Patched explicitly rather than relying on VOYAGE_API_KEY being unset
        # in the ambient environment — a real key may legitimately be present
        # (e.g. during live verification), and this test is about the
        # unconfigured *path*, not about what happens to be in .env.
        from app.core.config import Settings

        monkeypatch.setattr(Settings, "embeddings_configured", property(lambda self: False))

        repository = _repository(signed_in, embedding_model="voyage-code-3")
        monkeypatch.setattr(
            repositories_route.repository_repo, "get_by_id", lambda *a, **k: repository
        )
        monkeypatch.setattr(
            "app.services.retrieval.embedding_repo.count_embeddings", lambda *a, **k: 12
        )

        response = client.post(
            f"/api/v1/repositories/{repository.id}/search", json={"query": "auth"}
        )

        assert response.status_code == 501
        assert response.json()["error"]["code"] == "embedding_not_configured"
