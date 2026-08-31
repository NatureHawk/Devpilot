"""Change proposals: generate, review, and guard against stale snapshots.

Thin by design — investigation lives in :mod:`app.services.agent`, validation
and diffing in :mod:`app.services.patching`. This module owns the lifecycle and
the persistence around them.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, ConflictError
from app.models.change import ChangeStatus, ProposedChange
from app.models.repository import IndexingStatus, Repository
from app.models.user import User
from app.services.agent import InvestigationFailed, investigate
from app.services.llm import LLMProvider
from app.services.patching import (
    ValidatedPatch,
    assert_snapshot_current,
    validate_patch,
)
from app.services.tools import summarise_activity

logger = logging.getLogger(__name__)


def propose_change(
    session: Session,
    *,
    repository: Repository,
    user: User,
    request: str,
    settings: Settings,
    conversation_id: uuid.UUID | None = None,
    provider: LLMProvider | None = None,
) -> ProposedChange:
    """Investigate a request and persist a reviewable proposal.

    A failed investigation is still recorded, with status ``failed`` and a safe
    message. Silently dropping it would leave the user with a request that
    apparently never happened.
    """
    if repository.indexing_status != IndexingStatus.INDEXED:
        raise ConflictError(
            "This repository has not been indexed yet, so its code cannot be inspected.",
            details={"indexing_status": str(repository.indexing_status)},
        )

    proposal = ProposedChange(
        repository_id=repository.id,
        conversation_id=conversation_id,
        user_id=user.id,
        status=ChangeStatus.PROPOSED,
        request=request,
        # Pinned at generation time. Comparing this against the repository's
        # current sha later is what makes staleness detectable.
        indexed_commit_sha=repository.indexed_commit_sha,
    )

    try:
        result = investigate(
            session, repository=repository, request=request, settings=settings, provider=provider
        )
    except (InvestigationFailed, AppError) as exc:
        return _record_failure(session, proposal, exc)

    proposal.summary = result.summary
    proposal.model = result.model
    proposal.tool_calls_used = result.tool_calls_used
    proposal.investigation = summarise_activity(result.activity)

    if not result.edits:
        # The model investigated and concluded it could not make the change.
        # That is an answer, and the summary explains it.
        proposal.status = ChangeStatus.FAILED
        proposal.error = "No change could be derived from the repository evidence."
        proposal.edits = []
        proposal.diff = ""
        session.add(proposal)
        session.commit()
        return proposal

    try:
        patch: ValidatedPatch = validate_patch(
            session,
            repository_id=repository.id,
            edits=result.edits,
            # Only files actually opened during investigation may be edited.
            allowed_paths=set(result.files_read),
        )
    except AppError as exc:
        return _record_failure(session, proposal, exc)

    proposal.edits = [
        {
            "path": edit.path,
            "old_text": edit.old_text,
            "new_text": edit.new_text,
            "reason": edit.reason,
        }
        for edit in result.edits
    ]
    proposal.diff = patch.diff
    proposal.files_changed = len(patch.files)

    session.add(proposal)
    session.commit()

    logger.info(
        "Change proposed repository_id=%s change_id=%s files=%d tool_calls=%d model=%s",
        repository.id,
        proposal.id,
        proposal.files_changed,
        proposal.tool_calls_used,
        proposal.model,
    )
    return proposal


def review_change(
    session: Session,
    *,
    proposal: ProposedChange,
    repository: Repository,
    approve: bool,
) -> ProposedChange:
    """Record a human decision.

    Approval is an application state and nothing more: no branch, no commit, no
    pull request. Writing to GitHub is a later milestone, and pretending
    otherwise here would be the most dangerous thing this code could do.
    """
    if proposal.status in (ChangeStatus.APPROVED, ChangeStatus.REJECTED):
        raise ConflictError(
            "This change has already been reviewed.",
            details={"status": str(proposal.status)},
        )
    if proposal.status == ChangeStatus.FAILED:
        raise ConflictError("This change was never successfully generated.")

    # Re-checked at review time, not only at generation: the repository can be
    # re-indexed while a proposal sits waiting.
    if approve:
        assert_snapshot_current(
            proposal_sha=proposal.indexed_commit_sha,
            repository_sha=repository.indexed_commit_sha,
        )

    proposal.status = ChangeStatus.APPROVED if approve else ChangeStatus.REJECTED
    proposal.reviewed_at = datetime.now(UTC)
    session.commit()

    logger.info(
        "Change %s change_id=%s repository_id=%s",
        proposal.status.value,
        proposal.id,
        repository.id,
    )
    return proposal


def mark_stale_if_superseded(
    session: Session, *, proposal: ProposedChange, repository: Repository
) -> ProposedChange:
    """Flip a pending proposal to ``stale`` when the snapshot has moved on.

    Called when a proposal is read, so review screens never show a patch that
    can no longer be trusted as current.
    """
    if proposal.status != ChangeStatus.PROPOSED:
        return proposal
    if not proposal.indexed_commit_sha or not repository.indexed_commit_sha:
        return proposal

    if proposal.indexed_commit_sha != repository.indexed_commit_sha:
        proposal.status = ChangeStatus.STALE
        session.commit()
        logger.info(
            "Change marked stale change_id=%s proposed_against=%s current=%s",
            proposal.id,
            proposal.indexed_commit_sha,
            repository.indexed_commit_sha,
        )

    return proposal


def _record_failure(session: Session, proposal: ProposedChange, exc: Exception) -> ProposedChange:
    """Persist a failed attempt with a message that leaks nothing."""
    proposal.status = ChangeStatus.FAILED
    proposal.error = getattr(exc, "message", None) or "The change could not be generated."
    session.add(proposal)
    session.commit()

    logger.warning(
        "Change generation failed change_id=%s reason=%s", proposal.id, type(exc).__name__
    )
    return proposal
