"""The Ask flow: streaming assembly, persistence, bounded history, citation
persistence and provider-error handling.

The language model is a deterministic fake throughout — these tests are about
what ``ask_service.ask`` streams, what it writes to the database, and how it
behaves when the provider fails. Retrieval is stubbed so no embedding provider
or ``<=>`` query runs here; the real Gemini + pgvector + OpenRouter path is
covered by the separate manual verification, not by this suite.

Needs PostgreSQL: ``_persist_turn`` writes real ``messages`` / ``message_sources``
rows (and ``message_sources.chunk_id`` is a real FK into ``code_chunks``), and
asserting on those rows is the point.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import app.services.ask as ask_module
from app.api.deps import get_current_user
from app.db.session import get_engine, get_session_factory, session_scope
from app.models.conversation import Conversation, Message, MessageRole, MessageSource
from app.models.repository import IndexingStatus, Repository, RepositoryVisibility
from app.models.source import ChunkType, CodeChunk, SourceFile
from app.models.user import User
from app.services.ask import ask as ask_flow
from app.services.llm import LLMRateLimitError, StreamEvent, StreamEventType
from app.services.retrieval import RetrievalResult, RetrievalStrength, RetrievedSource

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
#  fakes
# --------------------------------------------------------------------------- #
class FakeLLM:
    """Streams a fixed answer word by word, then a DONE event with usage."""

    def __init__(
        self,
        text: str = "Sessions come from [S1] and [S2].",
        *,
        raises: Exception | None = None,
    ) -> None:
        self._text = text
        self._raises = raises
        self.received_messages: list = []
        self.received_system: str | None = None

    @property
    def model(self) -> str:
        return "fake/minimax-stand-in"

    def stream(self, *, system, messages, tools=(), max_tokens=None) -> Iterator[StreamEvent]:
        self.received_system = system
        self.received_messages = list(messages)
        if self._raises is not None:
            raise self._raises
        for word in self._text.split(" "):
            yield StreamEvent(type=StreamEventType.TEXT, text=word + " ")
        yield StreamEvent(
            type=StreamEventType.DONE, stop_reason="end_turn", input_tokens=42, output_tokens=7
        )

    def complete(self, *, system, messages, tools=(), max_tokens=None):  # pragma: no cover
        raise NotImplementedError


def _seed_real_chunks(db: Session, repo: Repository, n: int) -> list[CodeChunk]:
    """Persist ``n`` real chunks so a citation's chunk_id FK actually resolves."""
    src = SourceFile(
        id=uuid.uuid4(),
        repository_id=repo.id,
        path="app/mod.py",
        blob_sha=uuid.uuid4().hex,
        size_bytes=100,
        line_count=50,
        language="python",
        content="x",
        is_parsed=True,
    )
    db.add(src)
    db.flush()
    chunks = [
        CodeChunk(
            id=uuid.uuid4(),
            file_id=src.id,
            repository_id=repo.id,
            chunk_type=ChunkType.FUNCTION,
            node_type="function_definition",
            symbol=f"sym_{i}",
            parent_symbol=None,
            start_line=i * 10,
            end_line=i * 10 + 4,
            start_byte=0,
            end_byte=20,
            language="python",
            content=f"def sym_{i}():\n    return {i}",
        )
        for i in range(1, n + 1)
    ]
    db.add_all(chunks)
    db.flush()
    return chunks


def _retrieval_over(
    chunks: list[CodeChunk], strength: RetrievalStrength = RetrievalStrength.USEFUL
) -> RetrievalResult:
    sources = [
        RetrievedSource(
            rank=i,
            chunk_id=str(chunk.id),
            file_path=f"app/mod{i}.py",
            language="python",
            symbol=f"sym_{i}",
            parent_symbol=None,
            chunk_type="function",
            start_line=i * 10,
            end_line=i * 10 + 4,
            content=f"def sym_{i}():\n    return {i}",
            score=0.9 - i / 100,
        )
        for i, chunk in enumerate(chunks, start=1)
    ]
    return RetrievalResult(
        sources=sources, strength=strength, model="gemini-embedding-001", searched_chunks=55
    )


# --------------------------------------------------------------------------- #
#  db plumbing
# --------------------------------------------------------------------------- #
def _database_available() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return False
    return True


@pytest.fixture(scope="module", autouse=True)
def require_database() -> None:
    if not _database_available():
        pytest.skip("No reachable database at DATABASE_URL")


@pytest.fixture
def db() -> Iterator[Session]:
    session = get_session_factory()()
    yield session
    session.rollback()
    session.close()


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    def wipe() -> None:
        with get_session_factory()() as s:
            for table in (
                "message_sources",
                "messages",
                "conversations",
                "chunk_embeddings",
                "code_chunks",
                "files",
                "repositories",
                "users",
            ):
                s.execute(text(f"DELETE FROM {table}"))
            s.commit()

    wipe()
    yield
    wipe()


@pytest.fixture
def repo(db: Session) -> Repository:
    user = User(id=uuid.uuid4(), github_id=42, github_login="tester")
    db.add(user)
    db.flush()  # the user row must exist before the repository FK references it
    repository = Repository(
        id=uuid.uuid4(),
        owner="acme",
        name="api",
        provider="github",
        default_branch="main",
        visibility=RepositoryVisibility.PUBLIC,
        indexing_status=IndexingStatus.INDEXED,
        connected_by_user_id=user.id,
        embedding_model="gemini-embedding-001",
    )
    db.add(repository)
    db.commit()
    return repository


@pytest.fixture
def conversation(db: Session, repo: Repository) -> Conversation:
    conv = Conversation(id=uuid.uuid4(), repository_id=repo.id, user_id=repo.connected_by_user_id)
    db.add(conv)
    db.commit()
    return conv


@pytest.fixture
def make_retrieval(db: Session, repo: Repository):
    def _make(
        n: int = 3, strength: RetrievalStrength = RetrievalStrength.USEFUL
    ) -> RetrievalResult:
        return _retrieval_over(_seed_real_chunks(db, repo, n), strength)

    return _make


def _settings():
    from app.core.config import get_settings

    return get_settings()


# --------------------------------------------------------------------------- #
#  streaming assembly
# --------------------------------------------------------------------------- #
class TestStreamingAssembly:
    def test_emits_sources_first_then_text_then_done(
        self, db, repo, conversation, make_retrieval, monkeypatch
    ) -> None:
        retrieval = make_retrieval(3)
        monkeypatch.setattr(ask_module, "retrieve", lambda *a, **k: retrieval)

        chunks = list(
            ask_flow(
                db,
                repository=repo,
                question="Where are sessions made?",
                conversation=conversation,
                settings=_settings(),
                provider=FakeLLM(),
            )
        )

        assert chunks[0].type == "sources"
        assert chunks[-1].type == "done"
        text_chunks = [c for c in chunks if c.type == "text"]
        assert len(text_chunks) >= 2  # genuinely incremental, not one blob
        assembled = "".join(c.text for c in text_chunks)
        assert assembled.strip() == "Sessions come from [S1] and [S2]."

    def test_sources_event_carries_full_citation_metadata(
        self, db, repo, conversation, make_retrieval, monkeypatch
    ) -> None:
        retrieval = make_retrieval(2)
        monkeypatch.setattr(ask_module, "retrieve", lambda *a, **k: retrieval)

        chunks = list(
            ask_flow(
                db,
                repository=repo,
                question="q",
                conversation=conversation,
                settings=_settings(),
                provider=FakeLLM(),
            )
        )
        sources = chunks[0].sources
        assert [s["label"] for s in sources] == ["S1", "S2"]
        for s in sources:
            assert {
                "chunk_id",
                "file_path",
                "symbol",
                "start_line",
                "end_line",
                "score",
                "content",
            } <= s.keys()


# --------------------------------------------------------------------------- #
#  persistence
# --------------------------------------------------------------------------- #
class TestPersistence:
    def test_persists_user_then_assistant_turn_with_model_metadata(
        self, db, repo, conversation, make_retrieval, monkeypatch
    ) -> None:
        retrieval = make_retrieval(3)
        monkeypatch.setattr(ask_module, "retrieve", lambda *a, **k: retrieval)
        fake = FakeLLM("Answer text here [S1].")

        list(
            ask_flow(
                db,
                repository=repo,
                question="How does auth work?",
                conversation=conversation,
                settings=_settings(),
                provider=fake,
            )
        )

        rows = (
            db.query(Message)
            .filter_by(conversation_id=conversation.id)
            .order_by(Message.created_at)
            .all()
        )
        assert [m.role for m in rows] == [MessageRole.USER, MessageRole.ASSISTANT]
        assert rows[0].content == "How does auth work?"
        assert rows[1].content.strip() == "Answer text here [S1]."
        assert rows[1].model == "fake/minimax-stand-in"
        meta = rows[1].retrieval_metadata
        assert meta["strength"] == "useful"
        assert meta["sources"] == 3
        assert meta["embedding_model"] == "gemini-embedding-001"
        assert "authorization" not in json.dumps(meta).lower()

    def test_one_message_source_per_included_source_and_each_points_at_a_real_chunk(
        self, db, repo, conversation, make_retrieval, monkeypatch
    ) -> None:
        retrieval = make_retrieval(3)
        monkeypatch.setattr(ask_module, "retrieve", lambda *a, **k: retrieval)

        list(
            ask_flow(
                db,
                repository=repo,
                question="q",
                conversation=conversation,
                settings=_settings(),
                provider=FakeLLM(),
            )
        )

        assistant = (
            db.query(Message)
            .filter_by(conversation_id=conversation.id, role=MessageRole.ASSISTANT)
            .one()
        )
        cites = (
            db.query(MessageSource)
            .filter_by(message_id=assistant.id)
            .order_by(MessageSource.rank)
            .all()
        )

        assert [c.label for c in cites] == ["S1", "S2", "S3"]
        for citation, source in zip(cites, retrieval.sources, strict=True):
            assert str(citation.chunk_id) == source.chunk_id
            assert citation.file_path == source.file_path
            assert (citation.start_line, citation.end_line) == (source.start_line, source.end_line)
            assert citation.score == pytest.approx(source.score)
            # the FK resolved: the chunk really exists
            assert db.get(CodeChunk, citation.chunk_id) is not None

    def test_title_is_set_from_the_opening_question_once(
        self, db, repo, conversation, make_retrieval, monkeypatch
    ) -> None:
        retrieval = make_retrieval(1)
        monkeypatch.setattr(ask_module, "retrieve", lambda *a, **k: retrieval)

        list(
            ask_flow(
                db,
                repository=repo,
                question="First question?",
                conversation=conversation,
                settings=_settings(),
                provider=FakeLLM(),
            )
        )
        list(
            ask_flow(
                db,
                repository=repo,
                question="Second question?",
                conversation=conversation,
                settings=_settings(),
                provider=FakeLLM(),
            )
        )

        db.refresh(conversation)
        assert conversation.title == "First question?"


# --------------------------------------------------------------------------- #
#  bounded conversation history
# --------------------------------------------------------------------------- #
class TestBoundedHistory:
    def test_only_the_most_recent_turns_are_replayed_to_the_provider(
        self, db, repo, conversation, make_retrieval, monkeypatch
    ) -> None:
        limit = _settings().conversation_history_turns
        # Seed well over the limit of prior turns, with explicit increasing
        # timestamps so "most recent" is unambiguous (a single commit gives every
        # row the same now(), and the UUID PK tiebreak is random).
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(limit + 8):
            db.add(
                Message(
                    conversation_id=conversation.id,
                    role=MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT,
                    content=f"prior-{i}",
                    created_at=base + timedelta(seconds=i),
                )
            )
        db.commit()

        retrieval = make_retrieval(2)
        monkeypatch.setattr(ask_module, "retrieve", lambda *a, **k: retrieval)
        fake = FakeLLM()
        list(
            ask_flow(
                db,
                repository=repo,
                question="the new question",
                conversation=conversation,
                settings=_settings(),
                provider=fake,
            )
        )

        replayed = [m.text for m in fake.received_messages]
        history = [t for t in replayed if t.startswith("prior-")]
        assert len(history) == limit  # bounded
        assert "prior-0" not in " ".join(replayed)  # oldest dropped
        assert f"prior-{limit + 7}" in " ".join(replayed)  # newest kept
        assert "the new question" in replayed[-1]  # the question comes last


# --------------------------------------------------------------------------- #
#  provider errors
# --------------------------------------------------------------------------- #
class TestProviderErrors:
    def test_rate_limit_from_provider_propagates_out_of_the_flow(
        self, db, repo, conversation, make_retrieval, monkeypatch
    ) -> None:
        retrieval = make_retrieval(2)
        monkeypatch.setattr(ask_module, "retrieve", lambda *a, **k: retrieval)
        fake = FakeLLM(raises=LLMRateLimitError("slow down"))

        with pytest.raises(LLMRateLimitError):
            list(
                ask_flow(
                    db,
                    repository=repo,
                    question="q",
                    conversation=conversation,
                    settings=_settings(),
                    provider=fake,
                )
            )

        # A failed turn is not persisted — a partial answer is not a turn.
        assert db.query(Message).filter_by(conversation_id=conversation.id).count() == 0

    def test_endpoint_turns_a_mid_stream_failure_into_an_sse_error_event(
        self, app: FastAPI, db, repo, make_retrieval, monkeypatch
    ) -> None:
        retrieval = make_retrieval(2)
        monkeypatch.setattr(ask_module, "retrieve", lambda *a, **k: retrieval)
        monkeypatch.setattr(
            ask_module,
            "get_llm_provider",
            lambda settings: FakeLLM(raises=LLMRateLimitError("provider is rate limited")),
        )

        user = db.get(User, repo.connected_by_user_id)
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[session_scope] = lambda: get_session_factory()()
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post(
                    f"/api/v1/repositories/{repo.id}/ask", json={"question": "hello?"}
                )
                assert resp.status_code == 200  # the stream had already started
                events = [
                    json.loads(line[len("data: ") :])
                    for line in resp.text.splitlines()
                    if line.startswith("data: ")
                ]
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(session_scope, None)

        assert events[0]["type"] == "sources"
        error = next(e for e in events if e["type"] == "error")
        assert error["code"] == "llm_rate_limited"
        assert "rate limit" in error["message"].lower()
        assert "Traceback" not in resp.text  # never leak internals


class TestConversationListing:
    """A conversation row is created when a question is accepted, but the turn is
    only persisted once the answer completes. An abandoned or failed ask must not
    leave a blank entry in the user's history."""

    def test_a_conversation_with_no_messages_is_not_listed(
        self, db: Session, repo: Repository, conversation: Conversation
    ) -> None:
        from app.repositories import conversation_repo

        assert conversation_repo.list_conversations(db, repository_id=repo.id) == []

    def test_a_conversation_with_a_turn_is_listed(
        self, db: Session, repo: Repository, conversation: Conversation
    ) -> None:
        from app.repositories import conversation_repo

        db.add(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content="why is it slow?",
            )
        )
        db.commit()

        listed = conversation_repo.list_conversations(db, repository_id=repo.id)
        assert [item.id for item in listed] == [conversation.id]

    def test_another_repository_conversation_is_not_listed(
        self, db: Session, repo: Repository, conversation: Conversation
    ) -> None:
        """The repository filter is authorization, not presentation."""
        import uuid as uuid_module

        from app.repositories import conversation_repo

        db.add(Message(conversation_id=conversation.id, role=MessageRole.USER, content="mine"))
        db.commit()

        assert conversation_repo.list_conversations(db, repository_id=uuid_module.uuid4()) == []
