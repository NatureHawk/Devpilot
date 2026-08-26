"""Generates and persists embeddings for a repository's chunks.

Runs as the final stage of indexing, after chunks are in the database. Reads
chunks back in bounded pages rather than holding the repository in memory, and
writes vectors as each batch returns.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.embedding import ChunkEmbedding
from app.models.source import CodeChunk, SourceFile
from app.repositories import embedding_repo
from app.services.embeddings import InputKind, build_embedding_text
from app.services.embeddings.provider import EmbeddingProvider

logger = logging.getLogger(__name__)

# Chunks pulled from the database at a time. Independent of the provider's batch
# size: this bounds memory, that bounds request size.
_PAGE_SIZE = 200


@dataclass(slots=True)
class EmbeddingReport:
    chunks_embedded: int = 0
    batches: int = 0
    model: str = ""
    dimensions: int = 0


@dataclass(frozen=True, slots=True)
class _ChunkRow:
    """The fields needed to build embedding text, without loading whole ORM objects."""

    id: uuid.UUID
    path: str
    language: str
    chunk_type: str
    symbol: str | None
    parent_symbol: str | None
    content: str


def embed_repository_chunks(
    session: Session,
    *,
    repository_id: uuid.UUID,
    repository_full_name: str,
    provider: EmbeddingProvider,
) -> EmbeddingReport:
    """Embed every chunk of a repository and store the vectors.

    Any provider failure propagates: the caller treats the whole indexing run as
    failed rather than leaving a repository with vectors for some chunks and not
    others, which would silently return partial search results.
    """
    report = EmbeddingReport(model=provider.model, dimensions=provider.dimensions)

    # Vectors from a previous run are replaced wholesale, so a re-index can
    # never blend old and new embeddings.
    removed = embedding_repo.delete_repository_embeddings(session, repository_id)
    if removed:
        logger.info(
            "Cleared previous embeddings",
            extra={"repository_id": str(repository_id), "removed": removed},
        )

    for page in _iter_chunk_pages(session, repository_id):
        texts = [
            build_embedding_text(
                repository_full_name=repository_full_name,
                file_path=row.path,
                language=row.language,
                chunk_type=row.chunk_type,
                symbol=row.symbol,
                parent_symbol=row.parent_symbol,
                content=row.content,
            )
            for row in page
        ]

        result = provider.embed_texts(texts, kind=InputKind.DOCUMENT)
        report.batches += 1

        # Position is the contract: provider implementations must preserve
        # input order, which is what makes this zip correct.
        session.bulk_save_objects(
            [
                ChunkEmbedding(
                    chunk_id=row.id,
                    repository_id=repository_id,
                    model=result.model,
                    dimensions=result.dimensions,
                    embedding=vector,
                )
                for row, vector in zip(page, result.vectors, strict=True)
            ]
        )
        session.flush()
        report.chunks_embedded += len(page)

    logger.info(
        "Embeddings generated",
        extra={
            "repository_id": str(repository_id),
            "chunks_embedded": report.chunks_embedded,
            "model": report.model,
        },
    )
    return report


def _iter_chunk_pages(session: Session, repository_id: uuid.UUID) -> Iterator[list[_ChunkRow]]:
    """Page through a repository's chunks in a stable order.

    Keyset pagination on the primary key: an OFFSET scan would re-read earlier
    rows on every page, and the ordering keeps pages from overlapping.
    """
    last_id: uuid.UUID | None = None

    while True:
        stmt = (
            select(
                CodeChunk.id,
                SourceFile.path,
                CodeChunk.language,
                CodeChunk.chunk_type,
                CodeChunk.symbol,
                CodeChunk.parent_symbol,
                CodeChunk.content,
            )
            .join(SourceFile, SourceFile.id == CodeChunk.file_id)
            .where(CodeChunk.repository_id == repository_id)
            .order_by(CodeChunk.id)
            .limit(_PAGE_SIZE)
        )
        if last_id is not None:
            stmt = stmt.where(CodeChunk.id > last_id)

        rows = [
            _ChunkRow(
                id=row.id,
                path=row.path,
                language=row.language,
                chunk_type=str(row.chunk_type),
                symbol=row.symbol,
                parent_symbol=row.parent_symbol,
                content=row.content,
            )
            for row in session.execute(stmt)
        ]
        if not rows:
            return

        yield rows
        last_id = rows[-1].id
