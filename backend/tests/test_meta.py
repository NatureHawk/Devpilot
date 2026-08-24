"""Integration status must be reported honestly and without leaking secrets."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.services.integrations import describe_integrations


def test_integrations_endpoint_lists_known_integrations(client: TestClient) -> None:
    response = client.get("/api/v1/meta/integrations")
    assert response.status_code == 200

    names = {item["name"] for item in response.json()["integrations"]}
    assert names == {"github", "ai_provider"}


@pytest.mark.parametrize(
    ("client_id", "client_secret", "expected"),
    [("", "", False), ("id", "", False), ("id", "secret", True)],
)
def test_github_is_configured_only_when_both_credentials_are_present(
    client_id: str, client_secret: str, expected: bool
) -> None:
    settings = Settings(GITHUB_CLIENT_ID=client_id, GITHUB_CLIENT_SECRET=client_secret)
    github = next(i for i in describe_integrations(settings).integrations if i.name == "github")
    assert github.configured is expected


def test_integration_payload_never_contains_credential_values() -> None:
    settings = Settings(GITHUB_CLIENT_ID="id-123", GITHUB_CLIENT_SECRET="secret-456")
    payload = describe_integrations(settings).model_dump_json()
    assert "id-123" not in payload
    assert "secret-456" not in payload
