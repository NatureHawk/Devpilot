"""Turning an approved proposal into a branch, commit, and pull request.

The state machine is the security control here: nothing may reach GitHub
before ``approved``, two concurrent executions of the same proposal must not
both write, and a proposal that already has a PR must never get a second one.
These tests hold those lines, plus the Git Data API calls and the two
narrow, explicit safety nets (branch-name sanitisation, secret scanning).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable

import httpx
import pytest
from sqlalchemy.orm import Session

from app.integrations.github.client import GitHubClient
from app.integrations.github.errors import GitHubConflictError
from app.models.change import ChangeStatus, ProposedChange
from app.models.repository import IndexingStatus, Repository, RepositoryVisibility
from app.models.source import SourceFile
from app.models.user import User
from app.repositories import change_repo
from app.services.execution import ExecutionConflictError, execute_change
from app.services.execution.branching import generate_branch_name
from app.services.execution.secret_scan import scan_edits
from app.services.patching import ProposedEdit

pytestmark = pytest.mark.integration


# ---- branch naming ----------------------------------------------------------


class TestBranchNaming:
    def test_is_deterministic_for_the_same_proposal(self) -> None:
        proposal_id = uuid.uuid4()
        assert generate_branch_name(proposal_id, "Add validation") == generate_branch_name(
            proposal_id, "Add validation"
        )

    def test_uses_only_safe_ref_characters(self) -> None:
        name = generate_branch_name(uuid.uuid4(), "Fix ../../etc/passwd `rm -rf /` && evil")
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-/")
        assert set(name) <= allowed
        assert ".." not in name

    def test_bounded_length_even_for_a_very_long_summary(self) -> None:
        name = generate_branch_name(uuid.uuid4(), "x" * 5000)
        assert len(name) < 120

    def test_empty_summary_still_produces_a_valid_branch(self) -> None:
        name = generate_branch_name(uuid.uuid4(), "")
        assert name.startswith("devpilot/change/")
        assert not name.endswith("-")

    def test_two_different_proposals_never_collide(self) -> None:
        assert generate_branch_name(uuid.uuid4(), "same title") != generate_branch_name(
            uuid.uuid4(), "same title"
        )


# ---- secret scanning ---------------------------------------------------------


class TestSecretScan:
    def test_flags_a_github_token(self) -> None:
        edit = ProposedEdit(path="config.py", old_text="a", new_text="ghp_" + "a" * 36)
        finding = scan_edits([edit])
        assert finding is not None
        assert finding.path == "config.py"

    def test_flags_a_private_key(self) -> None:
        edit = ProposedEdit(
            path="id_rsa", old_text="a", new_text="-----BEGIN RSA PRIVATE KEY-----\nabc"
        )
        assert scan_edits([edit]) is not None

    def test_flags_an_env_file_by_path_alone(self) -> None:
        edit = ProposedEdit(path=".env", old_text="a", new_text="ORDINARY=value")
        finding = scan_edits([edit])
        assert finding is not None
        assert finding.kind == "an environment file"

    def test_does_not_flag_env_example(self) -> None:
        edit = ProposedEdit(path=".env.example", old_text="a", new_text="VOYAGE_API_KEY=")
        assert scan_edits([edit]) is None

    def test_ordinary_code_is_not_flagged(self) -> None:
        edit = ProposedEdit(
            path="app.py", old_text="a", new_text="def handler(request):\n    return 200"
        )
        assert scan_edits([edit]) is None

    def test_only_new_text_is_checked_not_old_text(self) -> None:
        """A secret already in the indexed source is not this commit's doing."""
        edit = ProposedEdit(path="a.py", old_text="ghp_" + "a" * 36, new_text="fine = 1")
        assert scan_edits([edit]) is None

    def test_finding_never_carries_the_matched_text(self) -> None:
        secret = "ghp_" + "a" * 36
        edit = ProposedEdit(path="a.py", old_text="x", new_text=secret)
        finding = scan_edits([edit])
        assert finding is not None
        assert secret not in finding.kind
        assert secret not in repr(finding)


# ---- GitHub client: the write path ------------------------------------------


def _write_path_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content or b"{}")

        if request.method == "GET" and "/git/commits/" in path:
            return httpx.Response(200, json={"sha": "base123", "tree": {"sha": "tree_base"}})
        if request.method == "POST" and path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": f"blob_{body['content'][:6]}"})
        if request.method == "POST" and path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "tree_new"})
        if request.method == "POST" and path.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "commit_new"})
        if request.method == "POST" and path.endswith("/git/refs"):
            if body.get("ref") == "refs/heads/exists-already":
                return httpx.Response(422, json={"message": "Reference already exists"})
            return httpx.Response(201, json={"ref": body["ref"]})
        if request.method == "POST" and path.endswith("/pulls"):
            return httpx.Response(
                201,
                json={
                    "id": 999,
                    "number": 42,
                    "html_url": "https://github.com/acme/widgets/pull/42",
                    "state": "open",
                    "head": {"ref": body["head"]},
                    "base": {"ref": body["base"]},
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {path}")

    return handler


@pytest.fixture
def write_client() -> GitHubClient:
    client = GitHubClient(token="t", transport=httpx.MockTransport(_write_path_handler()))
    yield client
    client.close()


class TestGitHubWritePath:
    def test_get_commit_tree_sha(self, write_client: GitHubClient) -> None:
        assert write_client.get_commit_tree_sha("acme", "widgets", "base123") == "tree_base"

    def test_create_blob_returns_a_sha(self, write_client: GitHubClient) -> None:
        assert write_client.create_blob("acme", "widgets", "hello") == "blob_hello"

    def test_create_tree_uses_the_base_tree(self, write_client: GitHubClient) -> None:
        sha = write_client.create_tree(
            "acme", "widgets", base_tree_sha="tree_base", entries=[]
        )
        assert sha == "tree_new"

    def test_create_commit_returns_a_sha(self, write_client: GitHubClient) -> None:
        sha = write_client.create_commit(
            "acme", "widgets", message="msg", tree_sha="tree_new", parent_sha="base123"
        )
        assert sha == "commit_new"

    def test_create_branch_succeeds(self, write_client: GitHubClient) -> None:
        write_client.create_branch(
            "acme", "widgets", branch="devpilot/change/abc", commit_sha="commit_new"
        )

    def test_create_branch_already_exists_is_a_conflict(self, write_client: GitHubClient) -> None:
        with pytest.raises(GitHubConflictError):
            write_client.create_branch("acme", "widgets", branch="exists-already", commit_sha="x")

    def test_create_pull_request_returns_the_pr(self, write_client: GitHubClient) -> None:
        pr = write_client.create_pull_request(
            "acme", "widgets", title="Add validation", body="body", head="devpilot/change/abc",
            base="main",
        )
        assert pr.number == 42
        assert pr.html_url == "https://github.com/acme/widgets/pull/42"
        assert pr.head_ref == "devpilot/change/abc"
        assert pr.base_ref == "main"


# ---- execute_change: full state machine, against a real database -----------


def database_available() -> bool:
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from app.db.session import get_engine

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
def db() -> Session:
    from app.db.session import get_session_factory

    session = get_session_factory()()
    yield session
    session.rollback()
    session.close()


@pytest.fixture(autouse=True)
def clean_tables() -> None:
    from sqlalchemy import text

    from app.db.session import get_session_factory

    def wipe() -> None:
        with get_session_factory()() as session:
            session.execute(text("DELETE FROM proposed_changes"))
            session.execute(text("DELETE FROM code_chunks"))
            session.execute(text("DELETE FROM files"))
            session.execute(text("DELETE FROM repositories"))
            session.execute(text("DELETE FROM users"))
            session.commit()

    wipe()
    yield
    wipe()


@pytest.fixture
def repository(db: Session) -> Repository:
    user = User(github_id=1, github_login="tester")
    db.add(user)
    db.commit()

    repo = Repository(
        owner="acme",
        name="widgets",
        provider="github",
        default_branch="main",
        visibility=RepositoryVisibility.PUBLIC,
        indexing_status=IndexingStatus.INDEXED,
        indexed_commit_sha="base123",
        connected_by_user_id=user.id,
    )
    db.add(repo)
    db.commit()

    db.add(
        SourceFile(
            repository_id=repo.id,
            path="app.py",
            blob_sha="s",
            size_bytes=20,
            line_count=1,
            language="python",
            content="def handler():\n    return 1\n",
            is_parsed=True,
        )
    )
    db.commit()
    return repo


def _approved_change(db: Session, repository: Repository, **overrides: object) -> ProposedChange:
    defaults: dict[str, object] = {
        "repository_id": repository.id,
        "status": ChangeStatus.APPROVED,
        "request": "Return 2 instead of 1",
        "summary": "Change the handler's return value",
        "indexed_commit_sha": repository.indexed_commit_sha,
        "edits": [
            {
                "path": "app.py",
                "old_text": "    return 1",
                "new_text": "    return 2",
                "reason": "requested change",
            }
        ],
        "diff": "--- a/app.py\n+++ b/app.py\n@@\n-    return 1\n+    return 2",
        "files_changed": 1,
    }
    defaults.update(overrides)
    change = ProposedChange(**defaults)
    db.add(change)
    db.commit()
    return change


class TestExecutionStateMachine:
    def test_approved_change_reaches_pr_created(self, db: Session, repository: Repository) -> None:
        change = _approved_change(db, repository)
        client = GitHubClient(token="t", transport=httpx.MockTransport(_write_path_handler()))

        result = execute_change(db, proposal=change, repository=repository, client=client)

        assert result.status == ChangeStatus.PR_CREATED
        assert result.pr_number == 42
        assert result.pr_url == "https://github.com/acme/widgets/pull/42"
        assert result.branch_name is not None
        assert result.commit_sha == "commit_new"
        events = [e["event"] for e in result.execution_events]
        assert events == [
            "execution_started",
            "branch_created",
            "patch_applied",
            "commit_created",
            "push_completed",
            "pr_created",
        ]

    def test_only_the_edited_file_is_sent_in_the_tree(
        self, db: Session, repository: Repository
    ) -> None:
        seen_entries: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if request.method == "POST" and path.endswith("/git/trees"):
                body = json.loads(request.content)
                seen_entries.extend(body["tree"])
            return _write_path_handler()(request)

        change = _approved_change(db, repository)
        client = GitHubClient(token="t", transport=httpx.MockTransport(handler))
        execute_change(db, proposal=change, repository=repository, client=client)

        assert [entry["path"] for entry in seen_entries] == ["app.py"]

    def test_not_approved_is_refused(self, db: Session, repository: Repository) -> None:
        from app.core.errors import ConflictError

        change = _approved_change(db, repository, status=ChangeStatus.PROPOSED)
        client = GitHubClient(token="t", transport=httpx.MockTransport(_write_path_handler()))

        with pytest.raises(ConflictError):
            execute_change(db, proposal=change, repository=repository, client=client)

    def test_stale_snapshot_is_marked_stale_not_applied(
        self, db: Session, repository: Repository
    ) -> None:
        change = _approved_change(db, repository, indexed_commit_sha="an-old-sha")
        client = GitHubClient(token="t", transport=httpx.MockTransport(_write_path_handler()))

        result = execute_change(db, proposal=change, repository=repository, client=client)

        assert result.status == ChangeStatus.STALE
        assert result.branch_name is None

    def test_secret_in_the_new_content_blocks_the_commit(
        self, db: Session, repository: Repository
    ) -> None:
        change = _approved_change(
            db,
            repository,
            edits=[
                {
                    "path": "app.py",
                    "old_text": "    return 1",
                    "new_text": f"    return 2  # ghp_{'a' * 36}",
                    "reason": "x",
                }
            ],
        )
        client = GitHubClient(token="t", transport=httpx.MockTransport(_write_path_handler()))

        result = execute_change(db, proposal=change, repository=repository, client=client)

        assert result.status == ChangeStatus.FAILED
        assert result.branch_name is None
        assert "ghp_" not in (result.execution_error or "")

    def test_already_pr_created_is_idempotent(self, db: Session, repository: Repository) -> None:
        change = _approved_change(
            db,
            repository,
            status=ChangeStatus.PR_CREATED,
            branch_name="devpilot/change/abc",
            commit_sha="commit_new",
            pr_number=7,
            pr_url="https://github.com/acme/widgets/pull/7",
        )

        def fail_on_any_request(request: httpx.Request) -> httpx.Response:
            raise AssertionError("an already-created PR must not touch GitHub again")

        client = GitHubClient(token="t", transport=httpx.MockTransport(fail_on_any_request))
        result = execute_change(db, proposal=change, repository=repository, client=client)

        assert result.status == ChangeStatus.PR_CREATED
        assert result.pr_number == 7

    def test_committed_retries_only_pr_creation_not_the_commit(
        self, db: Session, repository: Repository
    ) -> None:
        blob_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal blob_calls
            if request.method == "POST" and request.url.path.endswith("/git/blobs"):
                blob_calls += 1
            return _write_path_handler()(request)

        change = _approved_change(
            db,
            repository,
            status=ChangeStatus.COMMITTED,
            branch_name="devpilot/change/abc123",
            commit_sha="commit_new",
        )
        client = GitHubClient(token="t", transport=httpx.MockTransport(handler))

        result = execute_change(db, proposal=change, repository=repository, client=client)

        assert result.status == ChangeStatus.PR_CREATED
        assert blob_calls == 0  # the commit step never re-ran

    def test_pr_creation_failure_preserves_committed_state_for_retry(
        self, db: Session, repository: Repository
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and request.url.path.endswith("/pulls"):
                return httpx.Response(502, json={"message": "boom"})
            return _write_path_handler()(request)

        change = _approved_change(db, repository)
        client = GitHubClient(token="t", transport=httpx.MockTransport(handler))

        result = execute_change(db, proposal=change, repository=repository, client=client)

        assert result.status == ChangeStatus.COMMITTED
        assert result.branch_name is not None
        assert result.commit_sha == "commit_new"
        assert result.execution_error

    def test_concurrent_execution_only_one_wins(self, db: Session, repository: Repository) -> None:
        """Simulates the race directly at the claim, which is the actual guard."""
        change = _approved_change(db, repository)

        first = change_repo.claim_for_execution(db, change.id)
        second = change_repo.claim_for_execution(db, change.id)

        assert first == ChangeStatus.APPROVED
        assert second is None

    def test_double_execute_after_success_is_a_no_op(
        self, db: Session, repository: Repository
    ) -> None:
        change = _approved_change(db, repository)
        client = GitHubClient(token="t", transport=httpx.MockTransport(_write_path_handler()))

        first = execute_change(db, proposal=change, repository=repository, client=client)
        second = execute_change(db, proposal=first, repository=repository, client=client)

        assert first.pr_number == second.pr_number
        assert second.status == ChangeStatus.PR_CREATED

    def test_executing_is_refused_as_a_conflict(self, db: Session, repository: Repository) -> None:
        change = _approved_change(db, repository, status=ChangeStatus.EXECUTING)
        client = GitHubClient(token="t", transport=httpx.MockTransport(_write_path_handler()))

        with pytest.raises(ExecutionConflictError):
            execute_change(db, proposal=change, repository=repository, client=client)

    def test_stuck_executing_past_the_window_can_be_reclaimed(
        self, db: Session, repository: Repository
    ) -> None:
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import text

        change = _approved_change(db, repository, status=ChangeStatus.EXECUTING)
        stuck_since = datetime.now(UTC) - timedelta(minutes=20)
        db.execute(
            text("UPDATE proposed_changes SET updated_at = :ts WHERE id = :id"),
            {"ts": stuck_since, "id": change.id},
        )
        db.commit()
        # The raw SQL above bypassed the ORM, which still holds `change` in its
        # identity map with the old (pre-update) `updated_at` in memory. Force
        # the next SELECT to actually re-read the row rather than reuse it.
        db.expire_all()

        resumed = change_repo.claim_for_execution(db, change.id)
        assert resumed == ChangeStatus.APPROVED
