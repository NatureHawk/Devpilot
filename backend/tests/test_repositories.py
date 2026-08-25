"""Repository routes: authorization and empty-dataset behaviour."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes import repositories as repositories_route
from app.db.session import session_scope
from app.models.user import User


@pytest.fixture
def signed_in(app: FastAPI) -> User:
    """Stand in for a signed-in account without touching the database."""
    user = User(id=uuid.uuid4(), github_id=1, github_login="tester")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[session_scope] = lambda: object()
    yield user
    app.dependency_overrides.clear()


def test_listing_requires_a_session(client: TestClient) -> None:
    response = client.get("/api/v1/repositories")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_listing_is_empty_for_a_fresh_account(
    client: TestClient, signed_in: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(repositories_route.repository_repo, "list_repositories", lambda *a, **k: [])
    monkeypatch.setattr(repositories_route.repository_repo, "count_repositories", lambda *a, **k: 0)

    response = client.get("/api/v1/repositories")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}


def test_unconnected_repository_returns_structured_404(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This lookup is public: the workspace shell renders before sign-in."""
    app.dependency_overrides[session_scope] = lambda: object()
    monkeypatch.setattr(
        repositories_route.repository_repo, "get_by_full_name", lambda *a, **k: None
    )
    try:
        response = client.get("/api/v1/repositories/acme/widgets")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    body = response.json()["error"]
    assert body["code"] == "not_found"
    assert body["details"] == {"owner": "acme", "name": "widgets"}
