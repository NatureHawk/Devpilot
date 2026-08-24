from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("DEVPILOT_ENV", "test")


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
