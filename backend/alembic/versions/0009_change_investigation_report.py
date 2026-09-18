"""Structured investigation report on proposed changes

Adds ``report``: the grounded investigation result behind a proposal — outcome,
root cause, cited evidence with locations, anchor locations, per-file blob shas
the patch was validated against, and the validation performed.

Additive. Existing proposals get an empty object and render as before.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "proposed_changes",
        sa.Column(
            "report",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("proposed_changes", "report")
