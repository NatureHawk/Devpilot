"""GitHub identity, indexing state, and indexed content

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# create_type=False: the type is created explicitly below. Without it,
# create_table would emit CREATE TYPE a second time and fail.
CHUNK_TYPE_VALUES = ("module", "class", "function", "method", "block")
chunk_type = postgresql.ENUM(*CHUNK_TYPE_VALUES, name="chunk_type", create_type=False)

_NEW_INDEXING_STATES = ("not_indexed", "indexing", "indexed", "failed")
_OLD_INDEXING_STATES = ("not_indexed", "queued", "indexing", "indexed", "failed")


def _replace_indexing_status_enum(values: tuple[str, ...]) -> None:
    """Swap the indexing_status enum for one with a different value set.

    PostgreSQL can add enum values in place but cannot remove them, so the type
    is rebuilt and the column re-cast through text.
    """
    literals = ", ".join(f"'{value}'" for value in values)
    op.execute("ALTER TYPE indexing_status RENAME TO indexing_status_old")
    op.execute(f"CREATE TYPE indexing_status AS ENUM ({literals})")
    op.execute(
        "ALTER TABLE repositories ALTER COLUMN indexing_status "
        "TYPE indexing_status USING indexing_status::text::indexing_status"
    )
    op.execute("DROP TYPE indexing_status_old")


def upgrade() -> None:
    # ---- users: GitHub becomes the identity provider ------------------------
    # Email is no longer guaranteed: GitHub only releases it for accounts with a
    # public address, so identity keys on github_id instead.
    op.alter_column("users", "email", existing_type=sa.String(length=320), nullable=True)
    op.add_column("users", sa.Column("github_id", sa.BigInteger(), nullable=True))
    op.add_column("users", sa.Column("github_token_encrypted", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("github_token_scopes", sa.String(length=500), nullable=True))
    op.create_unique_constraint("uq_users_github_id", "users", ["github_id"])
    op.create_index("ix_users_github_id", "users", ["github_id"])

    # ---- repositories: indexing state machine -------------------------------
    # "queued" is dropped: this milestone indexes synchronously, so nothing ever
    # sits in a queue, and an unreachable state is a lie in the schema.
    op.execute(
        "UPDATE repositories SET indexing_status = 'not_indexed' WHERE indexing_status = 'queued'"
    )
    _replace_indexing_status_enum(_NEW_INDEXING_STATES)

    op.add_column(
        "repositories", sa.Column("indexing_started_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "repositories", sa.Column("indexed_commit_sha", sa.String(length=40), nullable=True)
    )
    op.add_column("repositories", sa.Column("indexing_error", sa.Text(), nullable=True))
    for column in ("indexed_file_count", "indexed_parsed_file_count", "indexed_chunk_count"):
        op.add_column(
            "repositories",
            sa.Column(column, sa.Integer(), nullable=False, server_default="0"),
        )
        # The default exists only to backfill existing rows; the application
        # always supplies a value.
        op.alter_column("repositories", column, server_default=None)

    # ---- files --------------------------------------------------------------
    op.create_table(
        "files",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("path", sa.String(length=1000), nullable=False),
        sa.Column("blob_sha", sa.String(length=40), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=50), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_parsed", sa.Boolean(), nullable=False),
        sa.Column("parse_error", sa.Text(), nullable=True),
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
            ["repository_id"],
            ["repositories.id"],
            name="fk_files_repository_id_repositories",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_files"),
        # Re-indexing replaces a repository's rows; a path can never appear twice.
        sa.UniqueConstraint("repository_id", "path", name="uq_files_repository_id_path"),
    )
    op.create_index("ix_files_repository_id_language", "files", ["repository_id", "language"])

    # ---- code_chunks --------------------------------------------------------
    literals = ", ".join(f"'{value}'" for value in CHUNK_TYPE_VALUES)
    op.execute(f"CREATE TYPE chunk_type AS ENUM ({literals})")
    op.create_table(
        "code_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_type", chunk_type, nullable=False),
        sa.Column("node_type", sa.String(length=100), nullable=True),
        sa.Column("symbol", sa.String(length=300), nullable=True),
        sa.Column("parent_symbol", sa.String(length=300), nullable=True),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("start_byte", sa.Integer(), nullable=False),
        sa.Column("end_byte", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=50), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("part_index", sa.Integer(), nullable=False),
        sa.Column("part_count", sa.Integer(), nullable=False),
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
            ["file_id"], ["files.id"], name="fk_code_chunks_file_id_files", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name="fk_code_chunks_repository_id_repositories",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_code_chunks"),
    )
    op.create_index("ix_code_chunks_file_id_start_line", "code_chunks", ["file_id", "start_line"])
    op.create_index(
        "ix_code_chunks_repository_id_chunk_type", "code_chunks", ["repository_id", "chunk_type"]
    )


def downgrade() -> None:
    op.drop_index("ix_code_chunks_repository_id_chunk_type", table_name="code_chunks")
    op.drop_index("ix_code_chunks_file_id_start_line", table_name="code_chunks")
    op.drop_table("code_chunks")
    op.execute("DROP TYPE chunk_type")

    op.drop_index("ix_files_repository_id_language", table_name="files")
    op.drop_table("files")

    for column in ("indexed_chunk_count", "indexed_parsed_file_count", "indexed_file_count"):
        op.drop_column("repositories", column)
    op.drop_column("repositories", "indexing_error")
    op.drop_column("repositories", "indexed_commit_sha")
    op.drop_column("repositories", "indexing_started_at")
    _replace_indexing_status_enum(_OLD_INDEXING_STATES)

    op.drop_index("ix_users_github_id", table_name="users")
    op.drop_constraint("uq_users_github_id", "users", type_="unique")
    op.drop_column("users", "github_token_scopes")
    op.drop_column("users", "github_token_encrypted")
    op.drop_column("users", "github_id")
    op.execute(
        "UPDATE users SET email = 'unknown+' || id || '@example.invalid' WHERE email IS NULL"
    )
    op.alter_column("users", "email", existing_type=sa.String(length=320), nullable=False)
