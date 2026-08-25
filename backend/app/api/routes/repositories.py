"""Connected repositories, and indexing them.

Routes stay thin: resolve, authorize, delegate, respond. The indexing pipeline
lives in :mod:`app.services.indexing.service`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import AppSettings, CurrentUser, DbSession, GitHub
from app.core.errors import ErrorResponse, NotFoundError
from app.models.repository import IndexingStatus, Repository, RepositoryVisibility
from app.models.user import User
from app.repositories import index_repo, repository_repo
from app.schemas.common import ListResponse
from app.schemas.indexing import IndexRunResponse, IndexStatusResponse, LanguageCount
from app.schemas.repository import RepositoryConnectRequest, RepositoryRead
from app.services.indexing.service import index_repository

router = APIRouter(prefix="/repositories", tags=["repositories"])


def _require_repository(session: DbSession, repository_id: uuid.UUID, user: User) -> Repository:
    """Load a repository the caller is allowed to act on.

    A repository connected by someone else is reported as missing rather than
    forbidden, so the endpoint does not confirm that an id exists.
    """
    repository = repository_repo.get_by_id(session, repository_id)
    if repository is None or repository.connected_by_user_id != user.id:
        raise NotFoundError(
            "That repository is not connected to your workspace.",
            details={"repository_id": str(repository_id)},
        )
    return repository


@router.get("", response_model=ListResponse[RepositoryRead], summary="List connected repositories")
def list_repositories(
    session: DbSession,
    user: CurrentUser,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ListResponse[RepositoryRead]:
    rows = repository_repo.list_repositories(session, user_id=user.id, limit=limit, offset=offset)
    return ListResponse[RepositoryRead](
        items=[RepositoryRead.model_validate(row) for row in rows],
        total=repository_repo.count_repositories(session, user_id=user.id),
    )


@router.post(
    "",
    response_model=RepositoryRead,
    status_code=status.HTTP_201_CREATED,
    summary="Connect a GitHub repository",
    responses={404: {"model": ErrorResponse, "description": "No such repository on GitHub"}},
)
def connect_repository(
    session: DbSession,
    user: CurrentUser,
    client: GitHub,
    payload: RepositoryConnectRequest,
) -> RepositoryRead:
    """Record a GitHub repository against the signed-in account.

    GitHub is the source of truth for visibility and default branch; the client
    supplies only the address. Connecting an already-connected repository
    refreshes its metadata rather than failing.
    """
    remote = client.get_repository(payload.owner, payload.name)

    repository = repository_repo.get_by_full_name(session, owner=remote.owner, name=remote.name)
    if repository is None:
        repository = Repository(owner=remote.owner, name=remote.name, provider="github")
        session.add(repository)

    repository.default_branch = remote.default_branch
    repository.visibility = (
        RepositoryVisibility.PRIVATE if remote.private else RepositoryVisibility.PUBLIC
    )
    repository.connected_by_user_id = user.id
    session.commit()

    return RepositoryRead.model_validate(repository)


@router.post(
    "/{repository_id}/index",
    response_model=IndexRunResponse,
    summary="Index a repository (synchronous)",
    responses={
        404: {"model": ErrorResponse, "description": "Repository is not connected to you"},
        409: {"model": ErrorResponse, "description": "An indexing run is already under way"},
        429: {"model": ErrorResponse, "description": "GitHub rate limit reached"},
    },
)
def index(
    session: DbSession,
    user: CurrentUser,
    client: GitHub,
    settings: AppSettings,
    repository_id: uuid.UUID,
) -> IndexRunResponse:
    """Run the full indexing pipeline and return what it did.

    The request is held open for the duration: there is no worker queue in this
    milestone, and the response says so via ``synchronous`` on the status route.
    """
    repository = _require_repository(session, repository_id, user)

    report = index_repository(session, repository=repository, client=client, settings=settings)

    return IndexRunResponse(
        repository_id=report.repository_id,
        status=report.status,
        commit_sha=report.commit_sha,
        files_discovered=report.files_discovered,
        files_indexed=report.files_indexed,
        files_parsed=report.files_parsed,
        chunks_created=report.chunks_created,
        files_skipped=report.files_skipped,
        skipped_by_reason=report.skipped_by_reason,
        parse_failures=report.parse_failures,
        complete=report.complete,
        started_at=report.started_at,
        completed_at=report.completed_at,
        error=report.error,
    )


@router.get(
    "/{repository_id}/index",
    response_model=IndexStatusResponse,
    summary="Current indexing state",
    responses={404: {"model": ErrorResponse, "description": "Repository is not connected to you"}},
)
def index_status(
    session: DbSession, user: CurrentUser, repository_id: uuid.UUID
) -> IndexStatusResponse:
    repository = _require_repository(session, repository_id, user)

    # Counts come from the repository row, which the last successful run wrote.
    # An in-flight run has not updated them yet, so they describe the previous
    # index — which is exactly what is still queryable.
    return IndexStatusResponse(
        repository_id=repository.id,
        status=repository.indexing_status,
        commit_sha=repository.indexed_commit_sha,
        files_indexed=repository.indexed_file_count,
        files_parsed=repository.indexed_parsed_file_count,
        chunks_created=repository.indexed_chunk_count,
        started_at=repository.indexing_started_at,
        indexed_at=repository.indexed_at,
        error=repository.indexing_error,
    )


@router.get(
    "/{repository_id}/languages",
    response_model=list[LanguageCount],
    summary="Indexed file counts per language",
    responses={404: {"model": ErrorResponse, "description": "Repository is not connected to you"}},
)
def index_languages(
    session: DbSession, user: CurrentUser, repository_id: uuid.UUID
) -> list[LanguageCount]:
    repository = _require_repository(session, repository_id, user)
    if repository.indexing_status != IndexingStatus.INDEXED:
        return []
    return [
        LanguageCount(language=language, file_count=count)
        for language, count in index_repo.language_breakdown(session, repository.id)
    ]


# Declared last on purpose: "/{owner}/{name}" is two path segments and would
# otherwise capture "/{repository_id}/index" as owner=<uuid>, name="index".
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
