from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.repository import IndexingStatus, RepositoryVisibility


class RepositoryRead(BaseModel):
    """A connected repository as the API exposes it.

    ``owner`` and ``name`` are kept separate because they are separate route
    segments in the frontend; the display form is composed there.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str
    owner: str
    name: str
    default_branch: str
    visibility: RepositoryVisibility
    indexing_status: IndexingStatus
    indexed_at: datetime | None
    indexed_commit_sha: str | None
    indexing_started_at: datetime | None
    indexing_error: str | None
    indexed_file_count: int
    indexed_parsed_file_count: int
    indexed_chunk_count: int
    created_at: datetime


class RepositoryConnectRequest(BaseModel):
    """Connect an existing GitHub repository by its address.

    Metadata (visibility, default branch) is read from GitHub rather than
    accepted from the client, so a connected repository always reflects reality.
    """

    owner: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
