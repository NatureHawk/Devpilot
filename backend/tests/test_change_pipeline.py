"""The proposal pipeline end to end, against a real database.

A scripted model drives the real loop: real read_file and find_symbol against
indexed rows, real grounding, real anchor validation, a real diff, persistence,
and approval through the API. Only the model and the embedding-backed search
are replaced. Nothing here has a GitHub client at all — which is the point:
producing and approving a proposal must never need one.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator, Sequence
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_github_client
from app.core.config import Settings
from app.db.session import get_session_factory, session_scope
from app.models.change import ChangeStatus, ProposedChange
from app.models.repository import IndexingStatus, Repository, RepositoryVisibility
from app.models.source import ChunkType, CodeChunk, SourceFile
from app.models.user import User
from app.services import agent as agent_module
from app.services.changes import propose_change
from app.services.llm import Completion, ToolCall
from app.services.retrieval import RetrievalResult, RetrievalStrength

pytestmark = pytest.mark.integration

PRODUCT_LIST = """\
import { useState } from "react";

export function ProductList({ products, query }) {
  const visible = products.filter((product) => product.name.includes(query));
  const sorted = visible.sort((a, b) => a.price - b.price);
  return sorted.map((product) => <ProductRow key={product.id} product={product} />);
}

export function ProductRow({ product }) {
  return <li>{product.name}</li>;
}
"""

# Two identical lines, so a short quote is ambiguous.
ORDERS = """\
def create_order(payload):
    total = 0
    return save(payload)


def update_order(payload):
    total = 0
    return save(payload)
"""


def database_available() -> bool:
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


@pytest.fixture(autouse=True)
def clean_tables() -> Iterator[None]:
    def wipe() -> None:
        with get_session_factory()() as session:
            for table in ("proposed_changes", "code_chunks", "files", "repositories", "users"):
                session.execute(text(f"DELETE FROM {table}"))
            session.commit()

    wipe()
    yield
    wipe()


@pytest.fixture
def db() -> Iterator[Session]:
    session = get_session_factory()()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def repository(db: Session) -> Repository:
    user = User(github_id=7, github_login="reviewer")
    db.add(user)
    db.commit()

    repo = Repository(
        owner="acme",
        name="shop",
        provider="github",
        default_branch="main",
        visibility=RepositoryVisibility.PUBLIC,
        indexing_status=IndexingStatus.INDEXED,
        indexed_commit_sha="snap111",
        connected_by_user_id=user.id,
    )
    db.add(repo)
    db.commit()

    for path, content, language in (
        ("frontend/src/ProductList.jsx", PRODUCT_LIST, "javascript"),
        ("backend/orders.py", ORDERS, "python"),
    ):
        source = SourceFile(
            repository_id=repo.id,
            path=path,
            blob_sha=f"blob-{path}",
            size_bytes=len(content),
            line_count=content.count("\n"),
            language=language,
            content=content,
            is_parsed=True,
        )
        db.add(source)
        db.flush()
        if path.endswith(".jsx"):
            db.add(
                CodeChunk(
                    file_id=source.id,
                    repository_id=repo.id,
                    chunk_type=ChunkType.FUNCTION,
                    symbol="ProductList",
                    start_line=3,
                    end_line=7,
                    start_byte=0,
                    end_byte=10,
                    language=language,
                    content="\n".join(content.splitlines()[2:7]),
                )
            )
    db.commit()
    return repo


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, LLM_PROVIDER="groq", GROQ_API_KEY="k", **overrides)


class ScriptedLLM:
    def __init__(self, completions: Sequence[Completion]) -> None:
        self._completions = list(completions)
        self.calls = 0
        self.transcript: list[Any] = []

    @property
    def model(self) -> str:
        return "scripted"

    def stream(self, **_: object):  # pragma: no cover
        raise AssertionError("not used")

    def complete(self, *, system, messages, tools=(), max_tokens=None) -> Completion:
        self.transcript = list(messages)
        completion = self._completions[self.calls]
        self.calls += 1
        return completion


def _tool(tool: str, /, **arguments: object) -> Completion:
    return Completion(
        text="",
        tool_calls=[ToolCall(id=f"call-{tool}", name=tool, arguments=dict(arguments))],
        stop_reason="tool_calls",
    )


def _answer(**overrides: Any) -> Completion:
    payload: dict[str, Any] = {
        "outcome": "change_proposed",
        "summary": "Copy the products before sorting so render does not mutate props.",
        "root_cause": "ProductList sorts the filtered array in place on every render [S1].",
        "relevant_files": [{"path": "frontend/src/ProductList.jsx", "reason": "The list."}],
        "evidence": [
            {"source": "S1", "claim": "ProductList filters and sorts on every render."},
            {"source": "S2", "claim": "The sort happens on line 5."},
        ],
        "proposed_changes": [
            {
                "path": "frontend/src/ProductList.jsx",
                "old_text": "  const sorted = visible.sort((a, b) => a.price - b.price);",
                "new_text": "  const sorted = [...visible].sort((a, b) => a.price - b.price);",
                "reason": "Sort a copy.",
            }
        ],
        "expected_behavior": "Rendering no longer reorders the caller's array.",
        "confidence": "high",
    }
    payload.update(overrides)
    return Completion(f"```json\n{json.dumps(payload)}\n```", [], "stop")


@pytest.fixture(autouse=True)
def no_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retrieval needs an embedding provider; the seed is not what is under test."""
    monkeypatch.setattr(
        agent_module,
        "retrieve",
        lambda *a, **k: RetrievalResult(
            sources=[], strength=RetrievalStrength.NONE, model="m", searched_chunks=0
        ),
    )


def _propose(db: Session, repository: Repository, llm: ScriptedLLM, **settings: object):
    user = db.get(User, repository.connected_by_user_id)
    assert user is not None
    return propose_change(
        db,
        repository=repository,
        user=user,
        request="The product list is slow and reorders items. Fix it.",
        settings=_settings(**settings),
        provider=llm,  # type: ignore[arg-type]
    )


class TestProposal:
    def test_investigates_with_real_tools_and_produces_a_validated_diff(
        self, db: Session, repository: Repository
    ) -> None:
        llm = ScriptedLLM(
            [
                _tool("find_symbol", name="ProductList"),
                _tool("read_file", path="frontend/src/ProductList.jsx", start_line=1, end_line=12),
                _answer(),
            ]
        )

        change = _propose(db, repository, llm)

        assert change.status == ChangeStatus.PROPOSED, change.error
        assert change.tool_calls_used == 2
        assert [step["tool"] for step in change.investigation] == ["find_symbol", "read_file"]
        assert change.diff.splitlines()[:2] == [
            "--- a/frontend/src/ProductList.jsx",
            "+++ b/frontend/src/ProductList.jsx",
        ]
        assert "+  const sorted = [...visible].sort" in change.diff
        assert "-  const sorted = visible.sort" in change.diff

        report = change.report
        assert report["outcome"] == "change_proposed"
        assert report["confidence"] == "high"
        assert report["evidence"][0]["path"] == "frontend/src/ProductList.jsx"
        assert report["evidence"][0]["origin"] == "find_symbol"
        assert report["proposed_changes"] == [
            {
                "path": "frontend/src/ProductList.jsx",
                "start_line": 5,
                "end_line": 5,
                "reason": "Sort a copy.",
            }
        ]
        assert report["files"][0]["blob_sha"] == "blob-frontend/src/ProductList.jsx"
        assert report["validation"]["diff_verified"] is True
        assert report["investigation"]["tools_used"] == {"find_symbol": 1, "read_file": 1}

    def test_missing_anchor_fails_closed(self, db: Session, repository: Repository) -> None:
        bad = _answer(
            proposed_changes=[
                {
                    "path": "frontend/src/ProductList.jsx",
                    "old_text": "const sorted = products.sort();",
                    "new_text": "const sorted = [...products].sort();",
                }
            ]
        )
        llm = ScriptedLLM([_tool("read_file", path="frontend/src/ProductList.jsx"), bad, bad, bad])

        change = _propose(db, repository, llm)

        assert change.status == ChangeStatus.FAILED
        assert "does not appear" in (change.error or "")
        assert change.diff == ""

    def test_duplicate_anchor_fails_closed(self, db: Session, repository: Repository) -> None:
        bad = _answer(
            evidence=[{"source": "S1", "claim": "orders"}],
            proposed_changes=[
                {"path": "backend/orders.py", "old_text": "    total = 0\n", "new_text": ""}
            ],
        )
        llm = ScriptedLLM([_tool("read_file", path="backend/orders.py"), bad, bad, bad])

        change = _propose(db, repository, llm)

        assert change.status == ChangeStatus.FAILED
        assert "appears 2 times" in (change.error or "")

    def test_a_corrected_anchor_after_feedback_succeeds(
        self, db: Session, repository: Repository
    ) -> None:
        ambiguous = _answer(
            evidence=[{"source": "S1", "claim": "orders"}],
            proposed_changes=[
                {"path": "backend/orders.py", "old_text": "    total = 0\n", "new_text": ""}
            ],
        )
        precise = _answer(
            evidence=[{"source": "S1", "claim": "orders"}],
            proposed_changes=[
                {
                    "path": "backend/orders.py",
                    "old_text": "def update_order(payload):\n    total = 0\n",
                    "new_text": "def update_order(payload):\n",
                }
            ],
        )
        llm = ScriptedLLM([_tool("read_file", path="backend/orders.py"), ambiguous, precise])

        change = _propose(db, repository, llm)

        assert change.status == ChangeStatus.PROPOSED, change.error
        assert change.report["investigation"]["repair_attempts"] == 1
        assert change.report["proposed_changes"][0]["start_line"] == 6

    def test_editing_a_file_never_read_is_rejected(
        self, db: Session, repository: Repository
    ) -> None:
        llm = ScriptedLLM([_tool("find_symbol", name="ProductList")] + [_answer()] * 3)

        change = _propose(db, repository, llm)

        assert change.status == ChangeStatus.FAILED
        assert "never read" in (change.error or "")

    def test_editing_a_nonexistent_file_is_rejected(
        self, db: Session, repository: Repository
    ) -> None:
        ghost = _answer(
            proposed_changes=[{"path": "frontend/src/Ghost.jsx", "old_text": "a", "new_text": "b"}]
        )
        llm = ScriptedLLM(
            [
                _tool("read_file", path="frontend/src/ProductList.jsx"),
                _tool("read_file", path="frontend/src/Ghost.jsx"),
                ghost,
                ghost,
                ghost,
            ]
        )

        change = _propose(db, repository, llm)

        assert change.status == ChangeStatus.FAILED
        assert change.diff == ""
        # The tool told the model the file does not exist, and the proposal was refused.
        assert change.investigation[1]["ok"] is False

    def test_path_escape_in_a_tool_call_is_refused(
        self, db: Session, repository: Repository
    ) -> None:
        llm = ScriptedLLM(
            [
                _tool("read_file", path="../../../etc/passwd"),
                _answer(outcome="insufficient_evidence", proposed_changes=[]),
            ]
        )

        change = _propose(db, repository, llm)

        assert change.investigation[0] == {
            "tool": "read_file",
            "duration_ms": change.investigation[0]["duration_ms"],
            "ok": False,
            "detail": "path out of scope",
        }
        assert change.report["investigation"]["files_read"] == []

    def test_a_partially_read_file_reports_the_lines_still_unseen(
        self, db: Session, repository: Repository
    ) -> None:
        """A model that paginates a file by hand leaves gaps, then reports the code
        it never looked at as absent. Every read names what is still unseen."""
        seen: list[dict[str, Any]] = []

        llm = ScriptedLLM(
            [
                _tool("read_file", path="backend/orders.py", start_line=1, end_line=2),
                _tool("read_file", path="backend/orders.py", start_line=6, end_line=8),
                _answer(outcome="insufficient_evidence", proposed_changes=[]),
            ]
        )
        original_execute = agent_module.execute

        def capture(context: Any, call: Any) -> Any:
            result = original_execute(context, call)
            seen.append(json.loads(result.content))
            return result

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(agent_module, "execute", capture)
        try:
            _propose(db, repository, llm)
        finally:
            monkeypatch.undo()

        # First read stops at line 2: everything after it is reported unseen.
        assert seen[0]["unread_lines"] == "3-8"
        assert "start_line=3" in seen[0]["note"]
        # Second read jumps to 6, so the skipped 3-5 is still named.
        assert seen[1]["unread_lines"] == "3-5"

    def test_insufficient_evidence_records_an_explanation_and_no_patch(
        self, db: Session, repository: Repository
    ) -> None:
        llm = ScriptedLLM(
            [
                _tool("search_code", query="pagination"),
                _answer(
                    outcome="insufficient_evidence",
                    proposed_changes=[],
                    confidence="low",
                    missing_information="No pagination code is indexed.",
                ),
            ]
        )
        # search_code goes through retrieval, which needs embeddings.
        import app.services.tools.executor as executor_module

        original = executor_module.retrieval_service.retrieve
        executor_module.retrieval_service.retrieve = lambda *a, **k: RetrievalResult(  # type: ignore[assignment]
            sources=[], strength=RetrievalStrength.NONE, model="m", searched_chunks=0
        )
        try:
            change = _propose(db, repository, llm)
        finally:
            executor_module.retrieval_service.retrieve = original  # type: ignore[assignment]

        assert change.status == ChangeStatus.FAILED
        assert change.diff == "" and change.edits == []
        assert change.report["outcome"] == "insufficient_evidence"
        assert change.report["missing_information"] == "No pagination code is indexed."
        assert "enough evidence" in (change.error or "")

    def test_an_oversized_file_is_capped_on_a_line_boundary(
        self, db: Session, repository: Repository
    ) -> None:
        """A single read may never spend the whole budget, and a half-line would
        never match as an anchor."""
        big = "".join(f"const line{index} = {index};\n" for index in range(4000))
        db.add(
            SourceFile(
                repository_id=repository.id,
                path="src/generated/big.ts",
                blob_sha="blob-big",
                size_bytes=len(big),
                line_count=big.count("\n"),
                language="typescript",
                content=big,
                is_parsed=False,
            )
        )
        db.commit()

        captured: list[dict[str, Any]] = []
        llm = ScriptedLLM(
            [
                _tool("read_file", path="src/generated/big.ts"),
                _answer(outcome="insufficient_evidence", proposed_changes=[]),
            ]
        )
        original_execute = agent_module.execute
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            agent_module,
            "execute",
            lambda context, call: (
                captured.append(json.loads((result := original_execute(context, call)).content))
                or result
            ),
        )
        try:
            change = _propose(db, repository, llm)
        finally:
            monkeypatch.undo()

        payload = captured[0]
        settings = _settings()
        assert len(payload["content"]) <= settings.effective_agent_tool_result_chars
        assert payload["truncated"] is True
        assert payload["content"].endswith(";")  # whole lines only
        assert payload["unread_lines"]
        assert change.status == ChangeStatus.FAILED  # the scripted model declined

    def test_a_proposal_citing_only_invented_sources_is_rejected(
        self, db: Session, repository: Repository
    ) -> None:
        invented = _answer(evidence=[{"source": "S99", "claim": "imagined"}])
        llm = ScriptedLLM(
            [_tool("read_file", path="frontend/src/ProductList.jsx")] + [invented] * 3
        )

        change = _propose(db, repository, llm)

        assert change.status == ChangeStatus.FAILED
        assert change.diff == ""
        assert "cite evidence" in (change.error or "")

    def test_unsupported_request_produces_no_patch(
        self, db: Session, repository: Repository
    ) -> None:
        llm = ScriptedLLM([_answer(outcome="unsupported_request", proposed_changes=[])])

        change = _propose(db, repository, llm)

        assert change.status == ChangeStatus.FAILED
        assert change.report["outcome"] == "unsupported_request"
        assert change.diff == ""

    def test_tool_limit_is_enforced_across_the_investigation(
        self, db: Session, repository: Repository
    ) -> None:
        llm = ScriptedLLM(
            [
                _tool("read_file", path="frontend/src/ProductList.jsx"),
                _tool("find_symbol", name="ProductRow"),
                _tool("find_symbol", name="ProductList"),
                _answer(),
            ]
        )

        change = _propose(db, repository, llm, AGENT_MAX_TOOL_CALLS=2)

        assert change.tool_calls_used == 2
        assert change.report["investigation"]["hit_tool_limit"] is True
        # The third request arrived when no tools were offered and never ran.
        assert len(change.investigation) == 2


# ---- review through the API ------------------------------------------------------


@pytest.fixture
def api(app: FastAPI, db: Session, repository: Repository) -> Iterator[TestClient]:
    user = db.get(User, repository.connected_by_user_id)

    def no_github() -> Iterator[None]:
        raise AssertionError("reviewing a proposal must never construct a GitHub client")
        yield  # pragma: no cover

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[session_scope] = lambda: get_session_factory()()
    app.dependency_overrides[get_github_client] = no_github
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        for dependency in (get_current_user, session_scope, get_github_client):
            app.dependency_overrides.pop(dependency, None)


def _proposed(db: Session, repository: Repository) -> ProposedChange:
    llm = ScriptedLLM([_tool("read_file", path="frontend/src/ProductList.jsx"), _answer()])
    change = _propose(db, repository, llm)
    assert change.status == ChangeStatus.PROPOSED, change.error
    return change


class TestReview:
    def test_proposal_is_returned_with_report_and_diff(
        self, api: TestClient, db: Session, repository: Repository
    ) -> None:
        change = _proposed(db, repository)

        body = api.get(f"/api/v1/changes/{change.id}").json()

        assert body["status"] == "proposed"
        assert body["diff"].startswith("--- a/frontend/src/ProductList.jsx")
        assert body["report"]["root_cause"]
        assert body["report"]["proposed_changes"][0]["start_line"] == 5
        assert "edits" not in body

    def test_approval_rechecks_the_snapshot_and_writes_nothing(
        self, api: TestClient, db: Session, repository: Repository
    ) -> None:
        change = _proposed(db, repository)

        response = api.post(f"/api/v1/changes/{change.id}/approve")

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "approved"
        assert response.json()["branch_name"] is None
        assert response.json()["execution_events"] == []

    def test_approving_after_a_reindex_is_refused_as_stale(
        self, api: TestClient, db: Session, repository: Repository
    ) -> None:
        change = _proposed(db, repository)
        db.execute(
            update(Repository)
            .where(Repository.id == repository.id)
            .values(indexed_commit_sha="snap222")
        )
        db.commit()

        response = api.post(f"/api/v1/changes/{change.id}/approve")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "patch_stale"
        assert "refresh the investigation" in response.json()["error"]["message"].lower()
        db.expire_all()
        assert db.get(ProposedChange, change.id).status == ChangeStatus.STALE  # type: ignore[union-attr]

    def test_approving_after_the_file_changed_is_refused_as_stale(
        self, api: TestClient, db: Session, repository: Repository
    ) -> None:
        """Same commit sha, different file content: the blob pin catches it."""
        change = _proposed(db, repository)
        db.execute(
            update(SourceFile)
            .where(SourceFile.path == "frontend/src/ProductList.jsx")
            .values(blob_sha="blob-new", content=PRODUCT_LIST.replace("price", "cost"))
        )
        db.commit()

        response = api.post(f"/api/v1/changes/{change.id}/approve")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "patch_stale"

    def test_approving_twice_is_a_conflict(
        self, api: TestClient, db: Session, repository: Repository
    ) -> None:
        change = _proposed(db, repository)

        assert api.post(f"/api/v1/changes/{change.id}/approve").status_code == 200
        assert api.post(f"/api/v1/changes/{change.id}/approve").status_code == 409

    def test_a_failed_investigation_cannot_be_approved(
        self, api: TestClient, db: Session, repository: Repository
    ) -> None:
        llm = ScriptedLLM([_answer(outcome="unsupported_request", proposed_changes=[])])
        change = _propose(db, repository, llm)

        response = api.post(f"/api/v1/changes/{change.id}/approve")

        assert response.status_code == 409

    def test_another_users_change_reads_as_missing(
        self, app: FastAPI, db: Session, repository: Repository
    ) -> None:
        change = _proposed(db, repository)
        stranger = User(github_id=99, github_login=f"stranger-{uuid.uuid4().hex[:6]}")
        db.add(stranger)
        db.commit()

        app.dependency_overrides[get_current_user] = lambda: stranger
        app.dependency_overrides[session_scope] = lambda: get_session_factory()()
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                assert client.post(f"/api/v1/changes/{change.id}/approve").status_code == 404
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(session_scope, None)
