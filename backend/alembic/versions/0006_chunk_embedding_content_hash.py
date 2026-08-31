"""Content hash on chunk embeddings, for reuse across re-indexes

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chunk_embeddings", sa.Column("content_hash", sa.String(length=64)))
    # Every reuse lookup filters by exactly this triple before ranking by hash.
    op.create_index(
        "ix_chunk_embeddings_repository_id_model_content_hash",
        "chunk_embeddings",
        ["repository_id", "model", "content_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chunk_embeddings_repository_id_model_content_hash", table_name="chunk_embeddings"
    )
    op.drop_column("chunk_embeddings", "content_hash")
