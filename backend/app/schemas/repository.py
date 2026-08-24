from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

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
    created_at: datetime
