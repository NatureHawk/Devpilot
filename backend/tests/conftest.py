from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("DEVPILOT_ENV", "test")


def _isolate_test_database() -> None:
    """Point the suite at ``<database>_test`` unless DATABASE_URL is set explicitly.

    Integration tests wipe users, repositories and proposals between cases.
    Without this, they read DATABASE_URL from ``.env`` — the development
    database — and delete every connected repository a developer has. An
    explicit DATABASE_URL in the environment (as CI sets) is trusted as-is.
    """
    if "DATABASE_URL" in os.environ:
        return

    from sqlalchemy.engine import make_url

    from app.core.config import Settings

    url = make_url(Settings().database_url)
    if url.database and not url.database.endswith("_test"):
        url = url.set(database=f"{url.database}_test")
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)


_isolate_test_database()


@pytest.fixture(scope="session")
def app() -> FastAPI:
    from app.core.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # raise_server_exceptions=False lets the 500 handler produce a response
    # instead of the exception propagating into the test.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
