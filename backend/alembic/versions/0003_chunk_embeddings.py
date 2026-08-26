"""Chunk embeddings backed by pgvector

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Must match app.models.embedding.EMBEDDING_DIMENSIONS. pgvector fixes the width
# at the column level, so changing it is a migration, not a config change.
EMBEDDING_DIMENSIONS = 1024


def upgrade() -> None:
    # pgvector ships as trusted, so a database owner can enable it without
    # superuser. IF NOT EXISTS keeps this idempotent when an operator has
    # already installed it cluster-wide.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.add_column(
        "repositories",
        sa.Column(
            "indexed_embedding_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "repositories", sa.Column("embedding_model", sa.String(length=100), nullable=True)
    )

    op.create_table(
        "chunk_embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["code_chunks.id"],
            name="fk_chunk_embeddings_chunk_id_code_chunks",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name="fk_chunk_embeddings_repository_id_repositories",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chunk_embeddings"),
        # One vector per chunk per model; re-indexing replaces rather than
        # accumulating, and two models can coexist during a migration.
        sa.UniqueConstraint("chunk_id", "model", name="uq_chunk_embeddings_chunk_id_model"),
    )

    # Search always filters to one repository and one model before ranking.
    op.create_index(
        "ix_chunk_embeddings_repository_id_model",
        "chunk_embeddings",
        ["repository_id", "model"],
    )

    # HNSW rather than IVFFlat: it needs no training pass over existing rows,
    # which matters because the table is empty when this runs, and it gives
    # better recall at the repository-sized datasets this targets. Cosine
    # operator class, matching the distance used at query time — an index built
    # for a different metric would simply be ignored.
    op.execute(
        "CREATE INDEX ix_chunk_embeddings_embedding_hnsw "
        "ON chunk_embeddings USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_index("ix_chunk_embeddings_embedding_hnsw", table_name="chunk_embeddings")
    op.drop_index("ix_chunk_embeddings_repository_id_model", table_name="chunk_embeddings")
    op.drop_table("chunk_embeddings")
    op.drop_column("repositories", "embedding_model")
    op.drop_column("repositories", "indexed_embedding_count")
    # The extension is intentionally left installed: other schemas may rely on
    # it, and dropping it would cascade to their columns.
