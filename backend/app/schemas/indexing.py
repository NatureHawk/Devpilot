from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.repository import IndexingStatus


class IndexRunResponse(BaseModel):
    """Result of a completed indexing run.

    Indexing is synchronous in this milestone: this response is only produced
    after the run has finished, so ``status`` is never ``indexing`` here.
    """

    repository_id: uuid.UUID
    status: IndexingStatus
    commit_sha: str | None = None

    files_discovered: int = 0
    files_indexed: int = 0
    files_parsed: int = 0
    chunks_created: int = 0
    files_skipped: int = 0
    skipped_by_reason: dict[str, int] = Field(default_factory=dict)
    parse_failures: int = 0

    # False when at least one supported file could not be parsed. The index is
    # usable, but not everything that should have structure has it.
    complete: bool = True

    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class IndexStatusResponse(BaseModel):
    """Current indexing state, read from the repository row."""

    repository_id: uuid.UUID
    status: IndexingStatus
    commit_sha: str | None = None
    files_indexed: int = 0
    files_parsed: int = 0
    chunks_created: int = 0
    started_at: datetime | None = None
    indexed_at: datetime | None = None
    error: str | None = None
    # Reported so clients know a poll will not change on its own.
    synchronous: bool = True


class LanguageCount(BaseModel):
    language: str
    file_count: int
