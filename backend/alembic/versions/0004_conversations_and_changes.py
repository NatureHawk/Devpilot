"""Message citations, retrieval metadata, and proposed changes

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

change_status = sa.Enum("proposed", "approved", "rejected", "stale", "failed", name="change_status")


def upgrade() -> None:
    # ---- messages gain provenance ----------------------------------------
    op.add_column("messages", sa.Column("model", sa.String(length=100), nullable=True))
    op.add_column(
        "messages",
        sa.Column("retrieval_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # ---- citations --------------------------------------------------------
    op.create_table(
        "message_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Nullable with SET NULL: a re-index replaces chunk rows, and a
        # citation must still render afterwards from its copied location.
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=10), nullable=False),
        sa.Column("file_path", sa.String(length=1000), nullable=False),
        sa.Column("symbol", sa.String(length=300), nullable=True),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=50), nullable=True),
        sa.Column("score", sa.Float(), nullable=False),
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
            ["message_id"],
            ["messages.id"],
            name="fk_message_sources_message_id_messages",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["code_chunks.id"],
            name="fk_message_sources_chunk_id_code_chunks",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_message_sources"),
    )
    op.create_index("ix_message_sources_message_id_rank", "message_sources", ["message_id", "rank"])

    # ---- proposed changes -------------------------------------------------
    op.create_table(
        "proposed_changes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", change_status, nullable=False),
        sa.Column("request", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("indexed_commit_sha", sa.String(length=40), nullable=True),
        sa.Column(
            "edits",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("diff", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "investigation",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("tool_calls_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("files_changed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
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
            name="fk_proposed_changes_repository_id_repositories",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_proposed_changes_conversation_id_conversations",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_proposed_changes_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_proposed_changes"),
    )
    op.create_index(
        "ix_proposed_changes_repository_id_created_at",
        "proposed_changes",
        ["repository_id", "created_at"],
    )
    op.create_index("ix_proposed_changes_conversation_id", "proposed_changes", ["conversation_id"])
    op.create_index("ix_proposed_changes_user_id", "proposed_changes", ["user_id"])


def downgrade() -> None:
    op.drop_table("proposed_changes")
    op.drop_index("ix_message_sources_message_id_rank", table_name="message_sources")
    op.drop_table("message_sources")
    op.drop_column("messages", "retrieval_metadata")
    op.drop_column("messages", "model")

    change_status.drop(op.get_bind(), checkfirst=True)
