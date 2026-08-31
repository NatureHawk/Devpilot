"""Vector embeddings for code chunks.

A separate table rather than a column on ``code_chunks``, for three reasons:
a chunk is meaningful without a vector (indexing produces chunks before
embeddings exist), the vector width is a property of the model rather than of
the chunk, and re-embedding under a new model becomes a delete-and-insert on one
table instead of a rewrite of the chunk rows.
"""

from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.models.source import CodeChunk

# The stored vector width. pgvector requires a fixed dimension per column, so
# this is a schema-level commitment: changing EMBEDDING_DIMENSIONS away from it
# requires a migration, and the application refuses to mix widths at runtime.
EMBEDDING_DIMENSIONS = 1024


class ChunkEmbedding(Base, UUIDPrimaryKey, Timestamps):
    """One vector per chunk, per embedding model."""

    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        # One vector per chunk per model. Re-indexing replaces rather than
        # accumulating, and two models can coexist during a migration.
        UniqueConstraint("chunk_id", "model", name="chunk_id_model"),
        # Every similarity search is scoped to one repository and one model, so
        # the ANN index is only useful alongside this filter.
        Index("ix_chunk_embeddings_repository_id_model", "repository_id", "model"),
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("code_chunks.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalised so search filters and deletes never join through code_chunks.
    repository_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )

    # Recorded per row so vectors from different models are never compared.
    # `provider` ("voyage" | "gemini") is stored alongside `model` so a switch
    # of provider is impossible to blend even if two providers ever shared a
    # model name. Nullable: rows written before this column existed simply never
    # match on reuse, which is the correct conservative behaviour for them.
    provider: Mapped[str | None] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)

    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)

    # sha256 of the exact text sent to the provider (path, language, symbol and
    # content — see build_embedding_text). A re-index generates new chunk rows
    # with new ids even when nothing changed, so this — not chunk_id — is what
    # lets an unchanged chunk reuse its existing vector instead of paying for
    # another provider call. Nullable: rows written before this column existed
    # simply never match on reuse, which is the correct (if conservative) thing
    # for them to do.
    content_hash: Mapped[str | None] = mapped_column(String(64))

    chunk: Mapped[CodeChunk] = relationship()

    def __repr__(self) -> str:
        return f"<ChunkEmbedding chunk={self.chunk_id} model={self.model}>"
