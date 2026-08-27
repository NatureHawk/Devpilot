"""Proposed code changes awaiting human review."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey


class ChangeStatus(enum.StrEnum):
    """Lifecycle of a proposal.

    Terminal states are ``approved``, ``rejected`` and ``failed``. ``stale`` is
    not terminal — a re-index can make a proposal reviewable again only by
    regenerating it, so ``stale`` means "do not act on this", not "deleted".
    """

    # Investigation succeeded and a patch is waiting for review.
    PROPOSED = "proposed"
    # A human accepted it. Approval is an application state in this milestone:
    # nothing is written to GitHub.
    APPROVED = "approved"
    REJECTED = "rejected"
    # The repository was re-indexed after this was generated, so the patch was
    # built against source that is no longer current.
    STALE = "stale"
    # Investigation or patch generation did not produce a usable result.
    FAILED = "failed"


class ProposedChange(Base, UUIDPrimaryKey, Timestamps):
    """One change request, its investigation, and the patch it produced."""

    __tablename__ = "proposed_changes"
    __table_args__ = (
        # The changes list is always "this repository, newest first".
        Index("ix_proposed_changes_repository_id_created_at", "repository_id", "created_at"),
    )

    repository_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    # The thread this came from, when it started as a conversation turn.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    status: Mapped[ChangeStatus] = mapped_column(
        Enum(ChangeStatus, name="change_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=ChangeStatus.PROPOSED,
    )

    # What the user asked for, verbatim.
    request: Mapped[str] = mapped_column(Text, nullable=False)
    # The model's own short account of what it changed and why. Never its raw
    # reasoning — only the summary it wrote for a reviewer.
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # The commit the indexed snapshot came from. This is what makes staleness
    # detectable: if the repository's current indexed sha differs, the patch was
    # built against source that has since moved.
    indexed_commit_sha: Mapped[str | None] = mapped_column(String(40))

    # Structured edits, exactly as validated: path, old_text, new_text, reason.
    # JSONB because the shape is a plan, always read whole, never queried into.
    edits: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    # Rendered unified diff, stored rather than regenerated so review shows what
    # was actually approved even if the source later changes.
    diff: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Bounded record of what the investigation did: tool name, duration,
    # success. No file contents, no arguments that could carry source.
    investigation: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    tool_calls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_changed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    model: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(Text)

    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    repository: Mapped[Any] = relationship("Repository")

    def __repr__(self) -> str:
        return f"<ProposedChange {self.id} {self.status}>"
