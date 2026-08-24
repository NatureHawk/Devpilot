"""Repository routes render correctly against an empty dataset."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import repositories as repositories_route
from app.db.session import session_scope


def test_listing_is_empty_for_a_fresh_installation(
    app: FastAPI, client: TestClient, monkeypatch
) -> None:
    app.dependency_overrides[session_scope] = lambda: object()
    monkeypatch.setattr(repositories_route.repository_repo, "list_repositories", lambda *a, **k: [])
    monkeypatch.setattr(repositories_route.repository_repo, "count_repositories", lambda *a, **k: 0)
    try:
        response = client.get("/api/v1/repositories")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}


def test_unconnected_repository_returns_structured_404(
    app: FastAPI, client: TestClient, monkeypatch
) -> None:
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
