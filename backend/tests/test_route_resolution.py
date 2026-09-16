"""Which handler a URL reaches.

`GET /repositories/{owner}/{name}` is two free path segments, so it can capture
any other two-segment route under /repositories that is matched after it. That
once hid the conversation and proposal lists entirely: every list request was
answered as "Repository <id>/changes is not connected to DevPilot."

The handlers are told apart by their not-found messages — owner/name lookups
name the repository, id lookups do not — so these tests observe real routing
without depending on how FastAPI stores routes internally.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes import repositories as repositories_route
from app.db.session import session_scope
from app.models.user import User

BY_ID = "That repository is not connected to your workspace."


@pytest.fixture
def client(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    user = User(id=uuid.uuid4(), github_id=1, github_login="tester")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[session_scope] = lambda: object()
    # Both lookups find nothing, so each handler answers with its own 404.
    monkeypatch.setattr(repositories_route.repository_repo, "get_by_id", lambda *a, **k: None)
    monkeypatch.setattr(
        repositories_route.repository_repo, "get_by_full_name", lambda *a, **k: None
    )
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def message(client: TestClient, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 404
    return str(response.json()["error"]["message"])


@pytest.mark.parametrize("resource", ["conversations", "changes"])
def test_listing_by_repository_id_reaches_the_list_handler(
    client: TestClient, resource: str
) -> None:
    repository_id = uuid.uuid4()

    assert message(client, f"/api/v1/repositories/{repository_id}/{resource}") == BY_ID


@pytest.mark.parametrize("name", ["changes", "conversations", "widgets"])
def test_owner_and_name_lookups_still_reach_the_repository_handler(
    client: TestClient, name: str
) -> None:
    """Only a UUID id selects the list routes; a repository named "changes" stays a repository."""
    assert message(client, f"/api/v1/repositories/acme/{name}") == (
        f"Repository acme/{name} is not connected to DevPilot."
    )
