"""Grounded question answering and change proposals.

Thin: authorize, validate, delegate, stream. The orchestration lives in
:mod:`app.services.ask` and :mod:`app.services.changes`.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterator

from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse

from app.api.deps import AppSettings, CurrentUser, DbSession, GitHub
from app.core.errors import AppError, ErrorResponse, NotFoundError
from app.models.change import ChangeStatus
from app.models.repository import Repository
from app.models.user import User
from app.repositories import change_repo, conversation_repo, repository_repo
from app.schemas.ask import (
    AskRequest,
    ChangeRead,
    ChangeRequest,
    ConversationRead,
    MessageRead,
)
from app.schemas.common import ListResponse
from app.services import ask as ask_service
from app.services import changes as change_service
from app.services.execution import execute_change

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ask"])


def _require_repository(session: DbSession, repository_id: uuid.UUID, user: User) -> Repository:
    """Load a repository the caller may act on.

    Mirrors the repositories router: someone else's repository reads as missing
    rather than forbidden, so an id is never confirmed to exist.
    """
    repository = repository_repo.get_by_id(session, repository_id)
    if repository is None or repository.connected_by_user_id != user.id:
        raise NotFoundError(
            "That repository is not connected to your workspace.",
            details={"repository_id": str(repository_id)},
        )
    return repository


@router.post(
    "/repositories/{repository_id}/ask",
    summary="Ask a question about the repository (streams)",
    responses={
        404: {"model": ErrorResponse, "description": "Repository is not connected"},
        409: {"model": ErrorResponse, "description": "Repository is not indexed or searchable"},
        501: {"model": ErrorResponse, "description": "A required provider is not configured"},
    },
)
def ask_repository(
    session: DbSession,
    settings: AppSettings,
    user: CurrentUser,
    repository_id: uuid.UUID,
    payload: AskRequest,
) -> StreamingResponse:
    """Answer a question, streaming the response as it is generated.

    Server-sent events rather than a JSON body: the answer is produced
    incrementally and the caller should see it that way. Nothing is buffered and
    replayed — each chunk is forwarded as the model emits it.
    """
    repository = _require_repository(session, repository_id, user)

    # Configuration is checked up front so an unconfigured deployment returns a
    # normal error response rather than failing partway into a stream, where the
    # status code has already been sent.
    if not settings.llm_configured:
        from app.services.llm import LLMNotConfiguredError

        raise LLMNotConfiguredError("No language model is configured for this deployment.")
    if not settings.embeddings_configured:
        from app.services.embeddings import EmbeddingNotConfiguredError

        raise EmbeddingNotConfiguredError(
            "No embedding provider is configured, so the repository cannot be searched."
        )

    if payload.conversation_id is not None:
        conversation = conversation_repo.get_conversation(
            session, payload.conversation_id, repository_id=repository.id
        )
        if conversation is None:
            raise NotFoundError("That conversation does not exist for this repository.")
    else:
        conversation = conversation_repo.create_conversation(
            session, repository_id=repository.id, user_id=user.id
        )
        session.commit()

    def events() -> Iterator[str]:
        try:
            for chunk in ask_service.ask(
                session,
                repository=repository,
                question=payload.question,
                conversation=conversation,
                settings=settings,
            ):
                yield _sse(
                    {
                        "type": chunk.type,
                        **({"text": chunk.text} if chunk.text else {}),
                        **({"sources": chunk.sources} if chunk.sources else {}),
                        **(
                            {"conversation_id": chunk.conversation_id}
                            if chunk.conversation_id
                            else {}
                        ),
                        **({"message_id": chunk.message_id} if chunk.message_id else {}),
                    }
                )
        except AppError as exc:
            # The status line is already sent, so a mid-stream failure has to be
            # delivered as an event the client can render.
            logger.warning("Ask failed mid-stream: %s", exc.code)
            yield _sse({"type": "error", "code": exc.code, "message": exc.message})
        except Exception:
            logger.exception("Unexpected failure during ask")
            yield _sse(
                {
                    "type": "error",
                    "code": "internal_error",
                    "message": "An unexpected error occurred while answering.",
                }
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            # Stops nginx and similar from buffering the stream into one chunk.
            "X-Accel-Buffering": "no",
        },
    )


def _sse(payload: dict[str, object]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get(
    "/repositories/{repository_id:uuid}/conversations",
    response_model=ListResponse[ConversationRead],
    summary="List conversations for a repository",
)
def list_conversations(
    session: DbSession, user: CurrentUser, repository_id: uuid.UUID
) -> ListResponse[ConversationRead]:
    repository = _require_repository(session, repository_id, user)
    rows = conversation_repo.list_conversations(session, repository_id=repository.id)
    return ListResponse[ConversationRead](
        items=[ConversationRead.model_validate(row) for row in rows], total=len(rows)
    )


@router.get(
    "/repositories/{repository_id}/conversations/{conversation_id}",
    response_model=ConversationRead,
    summary="Read one conversation with its messages and citations",
    responses={404: {"model": ErrorResponse, "description": "Conversation does not exist"}},
)
def get_conversation(
    session: DbSession,
    user: CurrentUser,
    repository_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> ConversationRead:
    repository = _require_repository(session, repository_id, user)
    conversation = conversation_repo.get_conversation(
        session, conversation_id, repository_id=repository.id
    )
    if conversation is None:
        raise NotFoundError("That conversation does not exist for this repository.")

    messages = conversation_repo.list_messages(session, conversation.id)
    return ConversationRead(
        id=conversation.id,
        repository_id=conversation.repository_id,
        title=conversation.title,
        created_at=conversation.created_at,
        messages=[MessageRead.model_validate(message) for message in messages],
    )


# ---- change proposals ------------------------------------------------------


@router.post(
    "/repositories/{repository_id:uuid}/changes",
    response_model=ChangeRead,
    status_code=status.HTTP_201_CREATED,
    summary="Investigate a change request and propose a patch",
    responses={
        404: {"model": ErrorResponse, "description": "Repository is not connected"},
        409: {"model": ErrorResponse, "description": "Repository is not indexed"},
        501: {"model": ErrorResponse, "description": "A required provider is not configured"},
    },
)
def create_change(
    session: DbSession,
    settings: AppSettings,
    user: CurrentUser,
    repository_id: uuid.UUID,
    payload: ChangeRequest,
) -> ChangeRead:
    """Run a bounded investigation and return a proposal for review.

    Synchronous: the caller waits for the investigation. No worker
    infrastructure exists, and claiming otherwise in the API would be a lie.
    """
    repository = _require_repository(session, repository_id, user)

    proposal = change_service.propose_change(
        session,
        repository=repository,
        user=user,
        request=payload.request,
        settings=settings,
        conversation_id=payload.conversation_id,
    )
    return ChangeRead.model_validate(proposal)


@router.get(
    "/repositories/{repository_id:uuid}/changes",
    response_model=ListResponse[ChangeRead],
    summary="List proposed changes",
)
def list_changes(
    session: DbSession, user: CurrentUser, repository_id: uuid.UUID
) -> ListResponse[ChangeRead]:
    repository = _require_repository(session, repository_id, user)
    rows = change_repo.list_for_repository(session, repository_id=repository.id)

    # Reading the list is a chance to notice the snapshot moved.
    rows = [
        change_service.mark_stale_if_superseded(session, proposal=row, repository=repository)
        for row in rows
    ]
    return ListResponse[ChangeRead](
        items=[ChangeRead.model_validate(row) for row in rows], total=len(rows)
    )


@router.get(
    "/changes/{change_id}",
    response_model=ChangeRead,
    summary="Read one proposed change",
    responses={404: {"model": ErrorResponse, "description": "Change does not exist"}},
)
def get_change(session: DbSession, user: CurrentUser, change_id: uuid.UUID) -> ChangeRead:
    proposal = change_repo.get_by_id(session, change_id)
    if proposal is None:
        raise NotFoundError("That change does not exist.")

    repository = _require_repository(session, proposal.repository_id, user)
    proposal = change_service.mark_stale_if_superseded(
        session, proposal=proposal, repository=repository
    )
    return ChangeRead.model_validate(proposal)


@router.post(
    "/changes/{change_id}/approve",
    response_model=ChangeRead,
    summary="Approve a proposed change",
    responses={
        404: {"model": ErrorResponse, "description": "Change does not exist"},
        409: {"model": ErrorResponse, "description": "Already reviewed, or the snapshot is stale"},
    },
)
def approve_change(session: DbSession, user: CurrentUser, change_id: uuid.UUID) -> ChangeRead:
    """Record approval after re-checking the patch against the current snapshot.

    Approval is an application state. Nothing is committed, branched or pushed —
    that happens only through ``POST /changes/{id}/execute``, from ``approved``.
    """
    return _review(session, user, change_id, approve=True)


@router.post(
    "/changes/{change_id}/reject",
    response_model=ChangeRead,
    summary="Reject a proposed change",
    responses={
        404: {"model": ErrorResponse, "description": "Change does not exist"},
        409: {"model": ErrorResponse, "description": "Already reviewed"},
    },
)
def reject_change(session: DbSession, user: CurrentUser, change_id: uuid.UUID) -> ChangeRead:
    return _review(session, user, change_id, approve=False)


@router.post(
    "/changes/{change_id}/execute",
    response_model=ChangeRead,
    summary="Create a branch, commit, and pull request for an approved change",
    responses={
        404: {"model": ErrorResponse, "description": "Change does not exist"},
        409: {
            "model": ErrorResponse,
            "description": "Not approved, already executing, or the snapshot is stale",
        },
    },
)
def execute_change_route(
    session: DbSession, user: CurrentUser, client: GitHub, change_id: uuid.UUID
) -> ChangeRead:
    """Apply the exact approved patch and open a real GitHub pull request.

    Idempotent: re-calling this on an already-``pr_created`` change returns the
    existing PR rather than creating a second one, and re-calling it on a
    ``committed`` change (a prior PR-creation attempt failed) retries only
    that step. Nothing here regenerates the patch — the model is not
    consulted again; this applies exactly what was reviewed and approved.
    """
    proposal = change_repo.get_by_id(session, change_id)
    if proposal is None:
        raise NotFoundError("That change does not exist.")

    repository = _require_repository(session, proposal.repository_id, user)
    result = execute_change(session, proposal=proposal, repository=repository, client=client)
    return ChangeRead.model_validate(result)


def _review(session: DbSession, user: User, change_id: uuid.UUID, *, approve: bool) -> ChangeRead:
    proposal = change_repo.get_by_id(session, change_id)
    if proposal is None:
        raise NotFoundError("That change does not exist.")

    repository = _require_repository(session, proposal.repository_id, user)

    if proposal.status == ChangeStatus.STALE:
        from app.services.patching import STALE_MESSAGE, StaleSnapshotError

        raise StaleSnapshotError(STALE_MESSAGE)

    reviewed = change_service.review_change(
        session, proposal=proposal, repository=repository, approve=approve
    )
    return ChangeRead.model_validate(reviewed)
