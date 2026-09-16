"""Lexical (full-text) search over code chunks

Adds the application-normalised term strings written at indexing time and a
generated tsvector over them, with a GIN index for ``@@`` matching:

- ``lexical_names``: path, parent symbol and symbol terms (weight A)
- ``lexical_body``:  content terms (weight D)

Terms are normalised in Python (app.services.lexical) rather than by a SQL
expression, because PostgreSQL's parser tokenises identifiers inconsistently
(``get_db`` splits, ``useMemo`` does not, paths become one token).

Additive. Existing rows get empty strings and therefore match nothing until the
repository is re-indexed; retrieval reports that state instead of hiding it.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "code_chunks", sa.Column("lexical_names", sa.Text(), nullable=False, server_default="")
    )
    op.add_column(
        "code_chunks", sa.Column("lexical_body", sa.Text(), nullable=False, server_default="")
    )
    op.execute(
        "ALTER TABLE code_chunks ADD COLUMN search_vector tsvector GENERATED ALWAYS AS ("
        "setweight(to_tsvector('simple'::regconfig, lexical_names), 'A') || "
        "setweight(to_tsvector('simple'::regconfig, lexical_body), 'D')"
        ") STORED"
    )
    op.create_index(
        "ix_code_chunks_search_vector",
        "code_chunks",
        ["search_vector"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_code_chunks_search_vector", table_name="code_chunks")
    op.drop_column("code_chunks", "search_vector")
    op.drop_column("code_chunks", "lexical_body")
    op.drop_column("code_chunks", "lexical_names")
