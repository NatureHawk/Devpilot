"""Queries over ``chunk_embeddings``, including similarity search."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.orm import Session

from app.models.embedding import ChunkEmbedding
from app.models.source import CodeChunk, SourceFile


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One retrieved chunk with its location and score."""

    chunk_id: uuid.UUID
    file_path: str
    language: str
    symbol: str | None
    parent_symbol: str | None
    chunk_type: str
    start_line: int
    end_line: int
    content: str
    score: float


def delete_repository_embeddings(session: Session, repository_id: uuid.UUID) -> int:
    """Remove every vector for a repository.

    Called before persisting a new snapshot. Chunk deletion already cascades
    here, so this exists for the case where vectors are replaced without the
    chunks changing.
    """
    # CursorResult carries rowcount; the generic Result type does not, so the
    # cast tells the checker what a DELETE actually returns here.
    result = cast(
        CursorResult[Any],
        session.execute(
            delete(ChunkEmbedding).where(ChunkEmbedding.repository_id == repository_id)
        ),
    )
    return result.rowcount or 0


def fetch_reusable(
    session: Session,
    *,
    repository_id: uuid.UUID,
    provider: str,
    model: str,
    dimensions: int,
) -> dict[str, list[float]]:
    """Existing vectors for this repository, keyed by content hash.

    Called before the old index is deleted (the delete cascades to this same
    table), so a re-index can copy a chunk's vector forward instead of asking
    the provider for it again. Scoped to the exact ``provider``, ``model`` and
    ``dimensions`` as well as ``repository_id``: a vector produced by a
    different provider, model or width is never a valid substitute, so it is
    simply absent from the map rather than filtered out downstream. This is
    what makes a provider or model switch a full re-embed.
    """
    stmt = select(ChunkEmbedding.content_hash, ChunkEmbedding.embedding).where(
        ChunkEmbedding.repository_id == repository_id,
        ChunkEmbedding.provider == provider,
        ChunkEmbedding.model == model,
        ChunkEmbedding.dimensions == dimensions,
        ChunkEmbedding.content_hash.is_not(None),
    )
    return {
        content_hash: list(vector)
        for content_hash, vector in session.execute(stmt)
        if content_hash is not None
    }


def count_embeddings(
    session: Session, repository_id: uuid.UUID, *, model: str | None = None
) -> int:
    stmt = (
        select(func.count())
        .select_from(ChunkEmbedding)
        .where(ChunkEmbedding.repository_id == repository_id)
    )
    if model is not None:
        stmt = stmt.where(ChunkEmbedding.model == model)
    return session.scalar(stmt) or 0


def similarity_for_chunks(
    session: Session,
    *,
    chunk_ids: Sequence[uuid.UUID],
    query_vector: Sequence[float],
    model: str,
) -> dict[uuid.UUID, float]:
    """Cosine similarity of specific chunks to ``query_vector``.

    For candidates found lexically but outside the semantic pool, so every
    candidate carries its real semantic score rather than a placeholder.
    """
    if not chunk_ids:
        return {}
    distance = ChunkEmbedding.embedding.cosine_distance(query_vector)
    stmt = select(ChunkEmbedding.chunk_id, distance.label("distance")).where(
        ChunkEmbedding.chunk_id.in_(list(chunk_ids)),
        ChunkEmbedding.model == model,
    )
    return {row.chunk_id: 1.0 - float(row.distance) for row in session.execute(stmt)}


def search_similar_chunks(
    session: Session,
    *,
    repository_id: uuid.UUID,
    query_vector: Sequence[float],
    model: str,
    top_k: int,
) -> list[SearchHit]:
    """Rank a repository's chunks by cosine similarity to ``query_vector``.

    pgvector's ``<=>`` is cosine *distance* in ``[0, 2]``; similarity is
    ``1 - distance``, giving the conventional ``[-1, 1]`` where higher is
    closer. Ordering happens in the database on the indexed distance
    expression, so only ``top_k`` rows of source content are ever materialised
    rather than the whole table.

    Filtering by ``model`` is what stops vectors produced by different models —
    which are not comparable — from being ranked against each other.
    """
    distance = ChunkEmbedding.embedding.cosine_distance(query_vector)

    stmt = (
        select(
            CodeChunk.id,
            SourceFile.path,
            CodeChunk.language,
            CodeChunk.symbol,
            CodeChunk.parent_symbol,
            CodeChunk.chunk_type,
            CodeChunk.start_line,
            CodeChunk.end_line,
            CodeChunk.content,
            distance.label("distance"),
        )
        .join(CodeChunk, CodeChunk.id == ChunkEmbedding.chunk_id)
        .join(SourceFile, SourceFile.id == CodeChunk.file_id)
        .where(
            ChunkEmbedding.repository_id == repository_id,
            ChunkEmbedding.model == model,
        )
        .order_by(distance)
        .limit(top_k)
    )

    return [
        SearchHit(
            chunk_id=row.id,
            file_path=row.path,
            language=row.language,
            symbol=row.symbol,
            parent_symbol=row.parent_symbol,
            chunk_type=str(row.chunk_type),
            start_line=row.start_line,
            end_line=row.end_line,
            content=row.content,
            score=1.0 - float(row.distance),
        )
        for row in session.execute(stmt)
    ]
