"""Provider metadata on chunk embeddings

Records which hosted provider ("voyage" | "gemini") produced each vector,
alongside the model name already stored. Vectors from different providers or
models are not comparable; keeping the provider on the row makes mixing them
impossible rather than merely unlikely, and lets embedding reuse across a
re-index require an exact provider/model/dimension/content match.

Additive and non-destructive: the column is nullable, existing rows keep NULL
and simply never satisfy a reuse lookup (they are re-embedded on the next index,
which is already what a provider switch requires).

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chunk_embeddings", sa.Column("provider", sa.String(length=20), nullable=True))
    # Reuse lookups filter by exactly this set before ranking by content hash.
    op.drop_index(
        "ix_chunk_embeddings_repository_id_model_content_hash", table_name="chunk_embeddings"
    )
    op.create_index(
        "ix_chunk_embeddings_repository_id_provider_model_content_hash",
        "chunk_embeddings",
        ["repository_id", "provider", "model", "content_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chunk_embeddings_repository_id_provider_model_content_hash",
        table_name="chunk_embeddings",
    )
    op.create_index(
        "ix_chunk_embeddings_repository_id_model_content_hash",
        "chunk_embeddings",
        ["repository_id", "model", "content_hash"],
    )
    op.drop_column("chunk_embeddings", "provider")
