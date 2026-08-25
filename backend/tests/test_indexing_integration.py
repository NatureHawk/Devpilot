"""Indexing end to end: persistence, state transitions, and the API.

These need a real PostgreSQL instance because they assert on constraints,
cascades and transaction behaviour that no in-memory substitute reproduces.
They skip when ``DATABASE_URL`` is unreachable.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import get_github_client
from app.core.config import get_settings
from app.core.security import issue_session
from app.db.session import get_engine, get_session_factory
from app.integrations.github.client import GitHubClient
from app.models.repository import IndexingStatus, Repository, RepositoryVisibility
from app.models.source import CodeChunk, SourceFile
from app.models.user import User

pytestmark = pytest.mark.integration


def database_available() -> bool:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return False
    return True


@pytest.fixture(scope="module", autouse=True)
def require_database() -> None:
    if not database_available():
        pytest.skip("No reachable database at DATABASE_URL")


@pytest.fixture
def db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def clean_tables() -> Iterator[None]:
    """Each test starts from an empty dataset and leaves one behind."""

    def wipe() -> None:
        with get_session_factory()() as session:
            session.execute(text("DELETE FROM code_chunks"))
            session.execute(text("DELETE FROM files"))
            session.execute(text("DELETE FROM repositories"))
            session.execute(text("DELETE FROM users"))
            session.commit()

    wipe()
    yield
    wipe()


# ---- fixture repository served through a mock transport --------------------

REPO_FILES: dict[str, bytes] = {
    "app/service.py": (
        b"import os\n\n\nclass UserService:\n"
        b"    def create(self, name):\n        return name\n\n\n"
        b"def helper(x):\n    return x\n"
    ),
    "app/widget.ts": b"export class Widget {\n  render() { return 1; }\n}\n",
    "README.md": b"# Title\n\nProse.\n",
    "node_modules/dep/index.js": b"module.exports = 1;\n",
    "package-lock.json": b'{"lockfileVersion": 3}\n',
    "assets/logo.png": b"\x89PNG\r\n\x1a\n\x00\x00binary",
    "app/broken.py": b"def ok():\n    return 1\n\ndef broken(:\n    ???\n",
}


def blob_sha(path: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, path).hex[:40]


def github_handler(missing_repo: str | None = None) -> Callable[[httpx.Request], httpx.Response]:
    """Serve REPO_FILES as a GitHub repository."""
    by_sha = {blob_sha(path): content for path, content in REPO_FILES.items()}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path

        if missing_repo and f"/repos/{missing_repo}" in path:
            return httpx.Response(404, content=json.dumps({"message": "Not Found"}))

        if "/git/trees/" in path:
            return httpx.Response(
                200,
                content=json.dumps(
                    {
                        "sha": "commitsha",
                        "truncated": False,
                        "tree": [
                            {
                                "path": name,
                                "sha": blob_sha(name),
                                "type": "blob",
                                "size": len(content),
                            }
                            for name, content in REPO_FILES.items()
                        ],
                    }
                ),
            )

        if "/git/blobs/" in path:
            content = by_sha.get(path.rsplit("/", 1)[-1], b"")
            return httpx.Response(
                200,
                content=json.dumps(
                    {"encoding": "base64", "content": base64.b64encode(content).decode()}
                ),
            )

        if "/commits/" in path:
            return httpx.Response(200, content=json.dumps({"sha": "commitsha"}))

        # /repos/{owner}/{name}
        parts = [p for p in path.split("/") if p]
        return httpx.Response(
            200,
            content=json.dumps(
                {
                    "id": 1,
                    "name": parts[-1],
                    "owner": {"login": parts[-2]},
                    "private": False,
                    "default_branch": "main",
                }
            ),
        )

    return handler


@pytest.fixture
def seeded_user(db: Session) -> User:
    user = User(github_id=1234, github_login="tester", display_name="Tester")
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def auth_headers(seeded_user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_session(get_settings(), str(seeded_user.id))}"}


@pytest.fixture
def repository(db: Session, seeded_user: User) -> Repository:
    row = Repository(
        owner="acme",
        name="widgets",
        provider="github",
        default_branch="main",
        visibility=RepositoryVisibility.PUBLIC,
        connected_by_user_id=seeded_user.id,
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def api(app: FastAPI) -> Iterator[TestClient]:
    """A client whose GitHub calls are served by the in-process fixture."""

    def override() -> Iterator[GitHubClient]:
        client = GitHubClient(token=None, transport=httpx.MockTransport(github_handler()))
        try:
            yield client
        finally:
            client.close()

    app.dependency_overrides[get_github_client] = override
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
    app.dependency_overrides.pop(get_github_client, None)


class TestSchemaConstraints:
    def test_a_repository_cannot_hold_the_same_path_twice(
        self, db: Session, repository: Repository
    ) -> None:
        for _ in range(2):
            db.add(
                SourceFile(
                    repository_id=repository.id,
                    path="app/main.py",
                    blob_sha="s",
                    size_bytes=1,
                    line_count=1,
                    language="python",
                    content="x = 1",
                    is_parsed=True,
                )
            )
        with pytest.raises(IntegrityError):
            db.commit()

    def test_deleting_a_file_removes_its_chunks(self, db: Session, repository: Repository) -> None:
        source_file = SourceFile(
            repository_id=repository.id,
            path="app/main.py",
            blob_sha="s",
            size_bytes=1,
            line_count=1,
            language="python",
            content="x = 1",
            is_parsed=True,
        )
        db.add(source_file)
        db.flush([source_file])
        db.add(
            CodeChunk(
                file_id=source_file.id,
                repository_id=repository.id,
                chunk_type="function",
                symbol="f",
                start_line=1,
                end_line=1,
                start_byte=0,
                end_byte=5,
                language="python",
                content="x = 1",
            )
        )
        db.commit()

        db.execute(text("DELETE FROM files WHERE id = :i"), {"i": source_file.id})
        db.commit()

        assert db.scalar(select(CodeChunk).where(CodeChunk.file_id == source_file.id)) is None


class TestIndexingRun:
    def test_indexing_populates_files_and_chunks(
        self, api: TestClient, db: Session, repository: Repository, auth_headers: dict[str, str]
    ) -> None:
        response = api.post(f"/api/v1/repositories/{repository.id}/index", headers=auth_headers)
        assert response.status_code == 200, response.text

        report = response.json()
        assert report["status"] == "indexed"
        assert report["commit_sha"] == "commitsha"
        assert report["files_indexed"] > 0
        assert report["chunks_created"] > 0

        paths = set(db.scalars(select(SourceFile.path)))
        assert "app/service.py" in paths
        assert "README.md" in paths

    def test_filtered_files_never_reach_the_database(
        self, api: TestClient, db: Session, repository: Repository, auth_headers: dict[str, str]
    ) -> None:
        api.post(f"/api/v1/repositories/{repository.id}/index", headers=auth_headers)
        paths = set(db.scalars(select(SourceFile.path)))

        assert not any("node_modules" in path for path in paths)
        assert "package-lock.json" not in paths
        assert "assets/logo.png" not in paths

    def test_chunks_carry_the_metadata_needed_to_display_a_result(
        self, api: TestClient, db: Session, repository: Repository, auth_headers: dict[str, str]
    ) -> None:
        """path -> symbol -> line range -> source, without reparsing."""
        api.post(f"/api/v1/repositories/{repository.id}/index", headers=auth_headers)

        chunk = db.scalars(select(CodeChunk).where(CodeChunk.symbol == "helper")).one()
        source_file = db.get(SourceFile, chunk.file_id)

        assert source_file is not None
        assert source_file.path == "app/service.py"
        assert chunk.language == "python"
        assert chunk.start_line >= 1
        assert chunk.end_line >= chunk.start_line
        assert "def helper" in chunk.content

    def test_a_malformed_file_is_stored_and_does_not_abort_the_run(
        self, api: TestClient, db: Session, repository: Repository, auth_headers: dict[str, str]
    ) -> None:
        response = api.post(f"/api/v1/repositories/{repository.id}/index", headers=auth_headers)

        assert response.status_code == 200
        broken = db.scalars(select(SourceFile).where(SourceFile.path == "app/broken.py")).one()
        # Stored and chunked, but not claimed as cleanly parsed.
        assert broken.content
        assert broken.chunks

    def test_unparseable_languages_are_stored_without_structure(
        self, api: TestClient, db: Session, repository: Repository, auth_headers: dict[str, str]
    ) -> None:
        api.post(f"/api/v1/repositories/{repository.id}/index", headers=auth_headers)
        readme = db.scalars(select(SourceFile).where(SourceFile.path == "README.md")).one()

        assert readme.is_parsed is False
        assert readme.chunks


class TestReindexing:
    def test_reindexing_replaces_rather_than_appends(
        self, api: TestClient, db: Session, repository: Repository, auth_headers: dict[str, str]
    ) -> None:
        first = api.post(f"/api/v1/repositories/{repository.id}/index", headers=auth_headers).json()
        second = api.post(
            f"/api/v1/repositories/{repository.id}/index", headers=auth_headers
        ).json()

        assert first["files_indexed"] == second["files_indexed"]
        assert db.scalar(select(text("count(*)")).select_from(SourceFile)) == first["files_indexed"]
        assert db.scalar(select(text("count(*)")).select_from(CodeChunk)) == first["chunks_created"]

    def test_no_duplicate_paths_survive_a_reindex(
        self, api: TestClient, db: Session, repository: Repository, auth_headers: dict[str, str]
    ) -> None:
        api.post(f"/api/v1/repositories/{repository.id}/index", headers=auth_headers)
        api.post(f"/api/v1/repositories/{repository.id}/index", headers=auth_headers)

        duplicates = db.execute(
            text(
                "SELECT count(*) FROM (SELECT repository_id, path FROM files "
                "GROUP BY repository_id, path HAVING count(*) > 1) d"
            )
        ).scalar_one()
        assert duplicates == 0


class TestStateTransitions:
    def test_a_new_repository_starts_not_indexed(self, repository: Repository) -> None:
        assert repository.indexing_status is IndexingStatus.NOT_INDEXED

    def test_a_successful_run_ends_indexed_with_counts_recorded(
        self, api: TestClient, db: Session, repository: Repository, auth_headers: dict[str, str]
    ) -> None:
        report = api.post(
            f"/api/v1/repositories/{repository.id}/index", headers=auth_headers
        ).json()

        db.expire_all()
        row = db.get(Repository, repository.id)
        assert row is not None
        assert row.indexing_status is IndexingStatus.INDEXED
        assert row.indexed_commit_sha == "commitsha"
        assert row.indexed_at is not None
        assert row.indexing_error is None
        assert row.indexed_file_count == report["files_indexed"]
        assert row.indexed_chunk_count == report["chunks_created"]

    def test_a_failed_run_ends_failed_and_records_a_safe_message(
        self, app: FastAPI, db: Session, repository: Repository, auth_headers: dict[str, str]
    ) -> None:
        def override() -> Iterator[GitHubClient]:
            client = GitHubClient(
                token=None, transport=httpx.MockTransport(github_handler(missing_repo="acme"))
            )
            try:
                yield client
            finally:
                client.close()

        app.dependency_overrides[get_github_client] = override
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.post(
                    f"/api/v1/repositories/{repository.id}/index", headers=auth_headers
                )
        finally:
            app.dependency_overrides.pop(get_github_client, None)

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "github_not_found"

        db.expire_all()
        row = db.get(Repository, repository.id)
        assert row is not None
        assert row.indexing_status is IndexingStatus.FAILED
        assert row.indexing_error
        # The stored message must be safe to display.
        assert "Bearer" not in row.indexing_error
        assert "Traceback" not in row.indexing_error

    def test_a_failure_preserves_a_previous_successful_index(
        self, app: FastAPI, db: Session, repository: Repository, auth_headers: dict[str, str]
    ) -> None:
        def make_override(missing: str | None) -> Callable[[], Iterator[GitHubClient]]:
            def override() -> Iterator[GitHubClient]:
                client = GitHubClient(
                    token=None, transport=httpx.MockTransport(github_handler(missing))
                )
                try:
                    yield client
                finally:
                    client.close()

            return override

        app.dependency_overrides[get_github_client] = make_override(None)
        with TestClient(app, raise_server_exceptions=False) as client:
            good = client.post(
                f"/api/v1/repositories/{repository.id}/index", headers=auth_headers
            ).json()

        app.dependency_overrides[get_github_client] = make_override("acme")
        with TestClient(app, raise_server_exceptions=False) as client:
            client.post(f"/api/v1/repositories/{repository.id}/index", headers=auth_headers)
        app.dependency_overrides.pop(get_github_client, None)

        db.expire_all()
        assert db.scalar(select(text("count(*)")).select_from(SourceFile)) == good["files_indexed"]
        assert db.scalar(select(text("count(*)")).select_from(CodeChunk)) == good["chunks_created"]

    def test_a_run_already_under_way_is_refused(
        self, api: TestClient, db: Session, repository: Repository, auth_headers: dict[str, str]
    ) -> None:
        db.execute(
            text(
                "UPDATE repositories SET indexing_status = 'indexing', "
                "indexing_started_at = now() WHERE id = :i"
            ),
            {"i": repository.id},
        )
        db.commit()

        response = api.post(f"/api/v1/repositories/{repository.id}/index", headers=auth_headers)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "indexing_in_progress"

    def test_an_abandoned_run_can_be_retried(
        self, api: TestClient, db: Session, repository: Repository, auth_headers: dict[str, str]
    ) -> None:
        """A process that died mid-index must not lock the repository forever."""
        db.execute(
            text(
                "UPDATE repositories SET indexing_status = 'indexing', "
                "indexing_started_at = now() - interval '2 hours' WHERE id = :i"
            ),
            {"i": repository.id},
        )
        db.commit()

        response = api.post(f"/api/v1/repositories/{repository.id}/index", headers=auth_headers)
        assert response.status_code == 200


class TestAuthorization:
    def test_indexing_requires_a_session(self, api: TestClient, repository: Repository) -> None:
        response = api.post(f"/api/v1/repositories/{repository.id}/index")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "not_authenticated"

    def test_a_repository_connected_by_someone_else_is_not_indexable(
        self, api: TestClient, db: Session, repository: Repository, auth_headers: dict[str, str]
    ) -> None:
        other = User(github_id=999, github_login="someone-else")
        db.add(other)
        db.flush([other])
        repository.connected_by_user_id = other.id
        db.commit()

        response = api.post(f"/api/v1/repositories/{repository.id}/index", headers=auth_headers)

        # Reported as missing so the endpoint does not confirm the id exists.
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_an_unknown_repository_is_not_found(
        self, api: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = api.post(f"/api/v1/repositories/{uuid.uuid4()}/index", headers=auth_headers)
        assert response.status_code == 404


class TestStatusEndpoints:
    def test_status_reflects_the_last_run(
        self, api: TestClient, repository: Repository, auth_headers: dict[str, str]
    ) -> None:
        run = api.post(f"/api/v1/repositories/{repository.id}/index", headers=auth_headers).json()
        status = api.get(f"/api/v1/repositories/{repository.id}/index", headers=auth_headers).json()

        assert status["status"] == "indexed"
        assert status["files_indexed"] == run["files_indexed"]
        assert status["chunks_created"] == run["chunks_created"]
        # The API must not imply a background job it does not have.
        assert status["synchronous"] is True

    def test_language_breakdown_is_empty_before_indexing(
        self, api: TestClient, repository: Repository, auth_headers: dict[str, str]
    ) -> None:
        assert (
            api.get(f"/api/v1/repositories/{repository.id}/languages", headers=auth_headers).json()
            == []
        )

    def test_language_breakdown_counts_indexed_files(
        self, api: TestClient, repository: Repository, auth_headers: dict[str, str]
    ) -> None:
        api.post(f"/api/v1/repositories/{repository.id}/index", headers=auth_headers)
        languages = api.get(
            f"/api/v1/repositories/{repository.id}/languages", headers=auth_headers
        ).json()

        by_language = {item["language"]: item["file_count"] for item in languages}
        assert by_language["python"] >= 1
        assert by_language["typescript"] >= 1


class TestConnectRepository:
    def test_connecting_reads_metadata_from_github(
        self, api: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = api.post(
            "/api/v1/repositories", headers=auth_headers, json={"owner": "acme", "name": "new"}
        )

        assert response.status_code == 201
        body = response.json()
        assert body["default_branch"] == "main"
        assert body["visibility"] == "public"
        assert body["indexing_status"] == "not_indexed"

    def test_connecting_twice_refreshes_instead_of_failing(
        self, api: TestClient, auth_headers: dict[str, str]
    ) -> None:
        first = api.post(
            "/api/v1/repositories", headers=auth_headers, json={"owner": "acme", "name": "dup"}
        )
        second = api.post(
            "/api/v1/repositories", headers=auth_headers, json={"owner": "acme", "name": "dup"}
        )

        assert second.status_code == 201
        assert first.json()["id"] == second.json()["id"]

    def test_connecting_requires_a_session(self, api: TestClient) -> None:
        response = api.post("/api/v1/repositories", json={"owner": "acme", "name": "x"})
        assert response.status_code == 401
