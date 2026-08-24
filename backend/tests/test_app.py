"""The application boots and exposes the surface the frontend depends on."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_openapi_exposes_expected_routes(client: TestClient) -> None:
    """Also catches schema-level mistakes: bad response models, duplicate operation ids."""
    response = client.get("/openapi.json")
    assert response.status_code == 200

    schema = response.json()
    assert schema["info"]["title"] == "DevPilot API"
    assert {
        "/health",
        "/health/ready",
        "/api/v1/meta/integrations",
        "/api/v1/repositories",
        "/api/v1/repositories/{owner}/{name}",
    } <= set(schema["paths"])


def test_unknown_route_returns_structured_error(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
