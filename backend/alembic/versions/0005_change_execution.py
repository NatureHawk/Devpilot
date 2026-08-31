"""Change execution: branch, commit, and pull request state

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_VALUES = ("proposed", "approved", "rejected", "stale", "failed")
_NEW_VALUES = ("executing", "committed", "pr_created")


def upgrade() -> None:
    # Postgres 12+ allows ADD VALUE inside a transaction as long as the new
    # value is not used in that same transaction, which it is not here.
    for value in _NEW_VALUES:
        op.execute(f"ALTER TYPE change_status ADD VALUE IF NOT EXISTS '{value}'")

    op.add_column("proposed_changes", sa.Column("branch_name", sa.String(length=250)))
    op.add_column("proposed_changes", sa.Column("commit_sha", sa.String(length=40)))
    op.add_column("proposed_changes", sa.Column("pr_number", sa.Integer()))
    op.add_column("proposed_changes", sa.Column("pr_url", sa.String(length=500)))
    op.add_column("proposed_changes", sa.Column("pr_node_id", sa.String(length=64)))
    op.add_column(
        "proposed_changes", sa.Column("executed_at", sa.DateTime(timezone=True))
    )
    op.add_column("proposed_changes", sa.Column("execution_error", sa.Text()))
    op.add_column(
        "proposed_changes",
        sa.Column(
            "execution_events",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("proposed_changes", "execution_events")
    op.drop_column("proposed_changes", "execution_error")
    op.drop_column("proposed_changes", "executed_at")
    op.drop_column("proposed_changes", "pr_node_id")
    op.drop_column("proposed_changes", "pr_url")
    op.drop_column("proposed_changes", "pr_number")
    op.drop_column("proposed_changes", "commit_sha")
    op.drop_column("proposed_changes", "branch_name")

    # Enum values cannot be dropped in place: rebuild the type with only the
    # original values. Fails loudly if any row still carries a new value,
    # which is the correct behaviour — that data would otherwise be silently
    # truncated to nothing.
    op.execute("ALTER TYPE change_status RENAME TO change_status_old")
    new_enum = sa.Enum(*_OLD_VALUES, name="change_status")
    new_enum.create(op.get_bind())
    op.execute(
        "ALTER TABLE proposed_changes "
        "ALTER COLUMN status TYPE change_status USING status::text::change_status"
    )
    op.execute("DROP TYPE change_status_old")
