"""Health and readiness behaviour, including the database-down path."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db.session import session_scope


def test_health_reports_liveness(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "devpilot-api"
    assert body["version"]


def test_readiness_reports_503_when_database_is_down(app: FastAPI, client: TestClient) -> None:
    class BrokenSession:
        def execute(self, *_: object, **__: object) -> None:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    app.dependency_overrides[session_scope] = lambda: BrokenSession()
    try:
        response = client.get("/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    body = response.json()["error"]
    assert body["code"] == "service_unavailable"
    # The driver message can embed the connection string; it must not leak.
    assert "connection refused" not in body["message"]


@pytest.mark.integration
def test_readiness_against_real_database(client: TestClient) -> None:
    """Runs only when a PostgreSQL instance is actually reachable."""
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from app.db.session import get_engine

    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        pytest.skip("No reachable database at DATABASE_URL")

    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}
