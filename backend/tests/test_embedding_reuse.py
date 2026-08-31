"""Embedding reuse across re-indexes: the actual quota-efficiency fix.

A re-index always generates fresh `code_chunks` rows (fresh ids), even for a
file that did not change — so `chunk_id` can never be the key that says "this
is the same chunk as last time." Content hash is. These tests drive the real
embedder against a real database (chunk_embeddings has a pgvector column,
which nothing in-memory can substitute for) with a fake provider that records
exactly what it was asked to embed, so "no request was made for this chunk"
is something the test can actually observe.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.embedding import ChunkEmbedding
from app.models.repository import IndexingStatus, Repository, RepositoryVisibility
from app.models.source import ChunkType, CodeChunk, SourceFile
from app.repositories import embedding_repo
from app.services.embeddings import InputKind, build_embedding_text, content_hash
from app.services.embeddings.provider import EmbeddingResult
from app.services.indexing.embedder import embed_repository_chunks

pytestmark = pytest.mark.integration

# pgvector's column width is fixed regardless of the `dimensions` field value
# (see app.models.embedding.EMBEDDING_DIMENSIONS), so a real 1024-wide vector
# is required here even though the values themselves are meaningless.
DIMENSIONS = 1024
MODEL = "voyage-code-3"


def _vec(seed: float) -> list[float]:
    return [seed] * DIMENSIONS


class RecordingProvider:
    """Fails any input it is asked to embed twice for the same text — a
    stronger assertion than merely counting calls, and one that would catch a
    caller re-sending something reuse should already have covered."""

    provider = "voyage"

    def __init__(self, *, model: str = MODEL, provider: str = "voyage") -> None:
        self.model = model
        self.provider = provider
        self.dimensions = DIMENSIONS
        self.embedded_texts: list[str] = []

    def embed_texts(self, texts: list[str], *, kind: InputKind) -> EmbeddingResult:
        self.embedded_texts.extend(texts)
        vectors = [[float(len(text) % 7 + i) for i in range(DIMENSIONS)] for text in texts]
        return EmbeddingResult(
            vectors=vectors,
            model=self.model,
            dimensions=self.dimensions,
            provider=self.provider,
        )


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


@pytest.fixture
def db() -> Iterator[Session]:
    from app.db.session import get_session_factory

    session = get_session_factory()()
    yield session
    session.rollback()
    session.close()


@pytest.fixture(autouse=True)
def clean_tables() -> Iterator[None]:
    from app.db.session import get_session_factory

    def wipe() -> None:
        with get_session_factory()() as session:
            session.execute(text("DELETE FROM chunk_embeddings"))
            session.execute(text("DELETE FROM code_chunks"))
            session.execute(text("DELETE FROM files"))
            session.execute(text("DELETE FROM repositories"))
            session.commit()

    wipe()
    yield
    wipe()


@pytest.fixture
def repository(db: Session) -> Repository:
    repo = Repository(
        owner="acme",
        name="widgets",
        provider="github",
        default_branch="main",
        visibility=RepositoryVisibility.PUBLIC,
        indexing_status=IndexingStatus.INDEXED,
    )
    db.add(repo)
    db.commit()
    return repo


def _seed_chunk(db: Session, repository: Repository, *, path: str, content: str) -> CodeChunk:
    source_file = SourceFile(
        repository_id=repository.id,
        path=path,
        blob_sha=uuid.uuid4().hex,
        size_bytes=len(content),
        line_count=content.count("\n") + 1,
        language="python",
        content=content,
        is_parsed=True,
    )
    db.add(source_file)
    db.flush()

    chunk = CodeChunk(
        file_id=source_file.id,
        repository_id=repository.id,
        chunk_type=ChunkType.FUNCTION,
        node_type="function_definition",
        symbol="handler",
        parent_symbol=None,
        start_line=1,
        end_line=content.count("\n") + 1,
        start_byte=0,
        end_byte=len(content),
        language="python",
        content=content,
    )
    db.add(chunk)
    db.commit()
    return chunk


def _embedding_text(repository: Repository, chunk: CodeChunk) -> str:
    return build_embedding_text(
        repository_full_name=f"{repository.owner}/{repository.name}",
        file_path="app.py",
        language="python",
        chunk_type="function",
        symbol="handler",
        parent_symbol=None,
        content=chunk.content,
    )


class TestReuse:
    def test_unchanged_chunk_is_reused_not_reembedded(
        self, db: Session, repository: Repository
    ) -> None:
        chunk = _seed_chunk(db, repository, path="app.py", content="def handler():\n    return 1")
        digest = content_hash(_embedding_text(repository, chunk))
        reuse = {digest: _vec(1.0)}

        provider = RecordingProvider()
        report = embed_repository_chunks(
            db,
            repository_id=repository.id,
            repository_full_name=f"{repository.owner}/{repository.name}",
            provider=provider,
            reuse=reuse,
        )

        assert provider.embedded_texts == []  # no provider call at all
        assert report.chunks_reused == 1
        assert report.chunks_embedded == 1

        stored = db.query(ChunkEmbedding).filter_by(chunk_id=chunk.id).one()
        assert stored.embedding == _vec(1.0)
        assert stored.content_hash == digest

    def test_changed_content_forces_reembedding(self, db: Session, repository: Repository) -> None:
        chunk = _seed_chunk(db, repository, path="app.py", content="def handler():\n    return 2")
        # A hash from *different* content — the reuse map from a previous run
        # whose version of this chunk was something else.
        stale_digest = content_hash(
            build_embedding_text(
                repository_full_name=f"{repository.owner}/{repository.name}",
                file_path="app.py",
                language="python",
                chunk_type="function",
                symbol="handler",
                parent_symbol=None,
                content="def handler():\n    return 1",  # the old content
            )
        )
        reuse = {stale_digest: _vec(9.0)}

        provider = RecordingProvider()
        report = embed_repository_chunks(
            db,
            repository_id=repository.id,
            repository_full_name=f"{repository.owner}/{repository.name}",
            provider=provider,
            reuse=reuse,
        )

        assert len(provider.embedded_texts) == 1  # a real call was made
        assert report.chunks_reused == 0
        assert report.chunks_embedded == 1

        stored = db.query(ChunkEmbedding).filter_by(chunk_id=chunk.id).one()
        assert stored.embedding != _vec(9.0)

    def test_model_dimension_mismatch_is_never_offered_as_reusable(
        self, db: Session, repository: Repository
    ) -> None:
        """`fetch_reusable` itself is what enforces this: a vector recorded
        under a different model/width is filtered out at the query, so it
        never even reaches the reuse map a caller could accidentally honour."""
        chunk = _seed_chunk(db, repository, path="app.py", content="def handler():\n    return 1")
        digest = content_hash(_embedding_text(repository, chunk))

        # Persist an embedding for this exact content hash, but under a
        # different model than the one about to run.
        db.add(
            ChunkEmbedding(
                chunk_id=chunk.id,
                repository_id=repository.id,
                provider="voyage",
                model="a-different-model",
                dimensions=DIMENSIONS,
                embedding=_vec(5.0),
                content_hash=digest,
            )
        )
        db.commit()

        reuse = embedding_repo.fetch_reusable(
            db,
            repository_id=repository.id,
            provider="voyage",
            model=MODEL,
            dimensions=DIMENSIONS,
        )
        assert reuse == {}

        provider = RecordingProvider()
        report = embed_repository_chunks(
            db,
            repository_id=repository.id,
            repository_full_name=f"{repository.owner}/{repository.name}",
            provider=provider,
            reuse=reuse,
        )

        assert len(provider.embedded_texts) == 1
        assert report.chunks_reused == 0

    def test_fetch_reusable_filters_by_dimensions_too(
        self, db: Session, repository: Repository
    ) -> None:
        chunk = _seed_chunk(db, repository, path="app.py", content="def handler():\n    return 1")
        digest = content_hash(_embedding_text(repository, chunk))

        # `dimensions` is recorded metadata, independent of the pgvector
        # column's own fixed width — a row can validly carry a `dimensions`
        # value that does not match the current provider's, and that alone
        # must be enough to exclude it from reuse.
        db.add(
            ChunkEmbedding(
                chunk_id=chunk.id,
                repository_id=repository.id,
                provider="voyage",
                model=MODEL,
                dimensions=2048,
                embedding=_vec(1.0),
                content_hash=digest,
            )
        )
        db.commit()

        reuse = embedding_repo.fetch_reusable(
            db,
            repository_id=repository.id,
            provider="voyage",
            model=MODEL,
            dimensions=DIMENSIONS,
        )
        assert reuse == {}

    def test_fetch_reusable_filters_by_provider_too(
        self, db: Session, repository: Repository
    ) -> None:
        """A vector produced by another provider — even at the same model name
        and width, and for the same content — is never offered for reuse. This
        is what makes switching EMBEDDING_PROVIDER a full re-embed."""
        chunk = _seed_chunk(db, repository, path="app.py", content="def handler():\n    return 1")
        digest = content_hash(_embedding_text(repository, chunk))

        db.add(
            ChunkEmbedding(
                chunk_id=chunk.id,
                repository_id=repository.id,
                provider="gemini",
                model=MODEL,
                dimensions=DIMENSIONS,
                embedding=_vec(1.0),
                content_hash=digest,
            )
        )
        db.commit()

        assert (
            embedding_repo.fetch_reusable(
                db,
                repository_id=repository.id,
                provider="voyage",
                model=MODEL,
                dimensions=DIMENSIONS,
            )
            == {}
        )
        # ...but the same lookup for the provider that wrote it does find it.
        assert embedding_repo.fetch_reusable(
            db,
            repository_id=repository.id,
            provider="gemini",
            model=MODEL,
            dimensions=DIMENSIONS,
        ) == {digest: _vec(1.0)}

    def test_switching_provider_reembeds_every_chunk(
        self, db: Session, repository: Repository
    ) -> None:
        """End to end through the embedder: an index under one provider, then a
        re-index under another, must send every chunk to the new provider even
        though the content is byte-identical."""
        chunk = _seed_chunk(db, repository, path="app.py", content="def handler():\n    return 1")

        voyage = RecordingProvider(model="voyage-code-3", provider="voyage")
        embed_repository_chunks(
            db,
            repository_id=repository.id,
            repository_full_name=f"{repository.owner}/{repository.name}",
            provider=voyage,
        )
        assert len(voyage.embedded_texts) == 1

        # Gather reuse the way indexing_service does — for the *new* provider.
        reuse = embedding_repo.fetch_reusable(
            db,
            repository_id=repository.id,
            provider="gemini",
            model="gemini-embedding-001",
            dimensions=DIMENSIONS,
        )
        assert reuse == {}  # nothing from Voyage is eligible

        gemini = RecordingProvider(model="gemini-embedding-001", provider="gemini")
        report = embed_repository_chunks(
            db,
            repository_id=repository.id,
            repository_full_name=f"{repository.owner}/{repository.name}",
            provider=gemini,
            reuse=reuse,
        )
        assert len(gemini.embedded_texts) == 1
        assert report.chunks_reused == 0
        assert report.provider == "gemini"

        stored = db.query(ChunkEmbedding).filter_by(chunk_id=chunk.id).one()
        assert stored.provider == "gemini"
        assert stored.model == "gemini-embedding-001"

    def test_no_reuse_map_embeds_everything_as_before(
        self, db: Session, repository: Repository
    ) -> None:
        """The default (no `reuse` argument) must behave exactly as it did
        before this change — every chunk is embedded."""
        _seed_chunk(db, repository, path="app.py", content="def handler():\n    return 1")

        provider = RecordingProvider()
        report = embed_repository_chunks(
            db,
            repository_id=repository.id,
            repository_full_name=f"{repository.owner}/{repository.name}",
            provider=provider,
        )

        assert len(provider.embedded_texts) == 1
        assert report.chunks_reused == 0
        assert report.chunks_embedded == 1

    def test_mixed_page_only_fetches_the_chunks_that_need_it(
        self, db: Session, repository: Repository
    ) -> None:
        unchanged = _seed_chunk(db, repository, path="a.py", content="def a():\n    return 1")
        changed = _seed_chunk(db, repository, path="b.py", content="def b():\n    return 2")

        unchanged_digest = content_hash(
            build_embedding_text(
                repository_full_name=f"{repository.owner}/{repository.name}",
                file_path="a.py",
                language="python",
                chunk_type="function",
                symbol="handler",
                parent_symbol=None,
                content=unchanged.content,
            )
        )
        reuse = {unchanged_digest: _vec(2.0)}

        provider = RecordingProvider()
        report = embed_repository_chunks(
            db,
            repository_id=repository.id,
            repository_full_name=f"{repository.owner}/{repository.name}",
            provider=provider,
            reuse=reuse,
        )

        assert report.chunks_reused == 1
        assert report.chunks_embedded == 2
        assert len(provider.embedded_texts) == 1  # only the changed chunk

        reused_row = db.query(ChunkEmbedding).filter_by(chunk_id=unchanged.id).one()
        fresh_row = db.query(ChunkEmbedding).filter_by(chunk_id=changed.id).one()
        assert reused_row.embedding == _vec(2.0)
        assert fresh_row.content_hash != unchanged_digest
