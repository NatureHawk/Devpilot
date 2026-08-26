from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey


class RepositoryVisibility(enum.StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"


class IndexingStatus(enum.StrEnum):
    """Lifecycle of the repository's searchable representation.

    Transitions are the only legal moves::

        not_indexed -> indexing -> indexed
        not_indexed -> indexing -> failed
        indexed     -> indexing -> indexed | failed
        failed      -> indexing -> indexed | failed

    A failed run leaves any previously indexed rows untouched, so ``failed``
    means "the newest attempt failed", not "there is no index".
    """

    NOT_INDEXED = "not_indexed"
    INDEXING = "indexing"
    INDEXED = "indexed"
    FAILED = "failed"


class Repository(Base, UUIDPrimaryKey, Timestamps):
    """A source repository a user has connected to DevPilot.

    ``owner``/``name`` mirror the provider's addressing scheme, which is also
    how the frontend routes (``/repositories/{owner}/{name}``), so the pair is
    unique per provider.
    """

    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("provider", "owner", "name", name="provider_owner_name"),)

    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="github")
    owner: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False, default="main")
    visibility: Mapped[RepositoryVisibility] = mapped_column(
        Enum(
            RepositoryVisibility,
            name="repository_visibility",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=RepositoryVisibility.PRIVATE,
    )
    indexing_status: Mapped[IndexingStatus] = mapped_column(
        Enum(
            IndexingStatus, name="indexing_status", values_callable=lambda e: [m.value for m in e]
        ),
        nullable=False,
        default=IndexingStatus.NOT_INDEXED,
    )
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    indexing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The commit whose tree produced the current index. Lets a future run detect
    # that nothing changed, and lets the UI say exactly what was indexed.
    indexed_commit_sha: Mapped[str | None] = mapped_column(String(40))
    # Operator-facing summary of the last failure. Never carries a token or a
    # raw driver message; see IndexingError.safe_message.
    indexing_error: Mapped[str | None] = mapped_column(Text)

    # Counts from the last successful index, denormalised so the workspace
    # header does not aggregate two large tables on every page load.
    indexed_file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    indexed_parsed_file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    indexed_chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    indexed_embedding_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Which model produced the current vectors. Search compares against this, so
    # changing models without re-indexing yields no results rather than wrong ones.
    embedding_model: Mapped[str | None] = mapped_column(String(100))

    # The account that connected the repository. Shared access is modelled by a
    # membership table when multi-user workspaces land; a single owner column is
    # enough today and is cheap to migrate away from.
    connected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    def __repr__(self) -> str:
        return f"<Repository {self.full_name}>"
