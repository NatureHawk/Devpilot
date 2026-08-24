"""Connected repositories.

Reads only. Connecting a repository requires the GitHub integration, which is
not part of this milestone, so no write route exists yet.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import DbSession
from app.core.errors import ErrorResponse, NotFoundError
from app.repositories import repository_repo
from app.schemas.common import ListResponse
from app.schemas.repository import RepositoryRead

router = APIRouter(prefix="/repositories", tags=["repositories"])


@router.get("", response_model=ListResponse[RepositoryRead], summary="List connected repositories")
def list_repositories(
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ListResponse[RepositoryRead]:
    rows = repository_repo.list_repositories(session, limit=limit, offset=offset)
    return ListResponse[RepositoryRead](
        items=[RepositoryRead.model_validate(row) for row in rows],
        total=repository_repo.count_repositories(session),
    )


@router.get(
    "/{owner}/{name}",
    response_model=RepositoryRead,
    summary="Get one connected repository",
    responses={404: {"model": ErrorResponse, "description": "Repository is not connected"}},
)
def get_repository(session: DbSession, owner: str, name: str) -> RepositoryRead:
    row = repository_repo.get_by_full_name(session, owner=owner, name=name)
    if row is None:
        raise NotFoundError(
            f"Repository {owner}/{name} is not connected to DevPilot.",
            details={"owner": owner, "name": name},
        )
    return RepositoryRead.model_validate(row)
