"""Change proposals: generate, review, and guard against stale snapshots.

Thin by design — investigation lives in :mod:`app.services.agent`, result
grounding in :mod:`app.services.proposal`, validation and diffing in
:mod:`app.services.patching`. This module owns the lifecycle and the
persistence around them.

    investigate ──> proposed ──(human approves)──> approved ──(human ships)──> execution
         │              │
         └─> failed     └─> stale (the repository moved on) / rejected

Nothing in this module writes to GitHub. Approval is a recorded decision; the
branch, commit and pull request happen only in :mod:`app.services.execution`,
only from ``approved``, and only when a person asks.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, ConflictError
from app.models.change import ChangeStatus, ProposedChange
from app.models.repository import IndexingStatus, Repository
from app.models.user import User
from app.services.agent import InvestigationFailed, InvestigationResult, investigate
from app.services.llm import LLMProvider
from app.services.patching import StaleSnapshotError, revalidate_against_snapshot
from app.services.proposal import Outcome
from app.services.tools import summarise_activity

logger = logging.getLogger(__name__)

_NO_CHANGE_MESSAGES = {
    Outcome.INSUFFICIENT_EVIDENCE: (
        "DevPilot did not find enough evidence in the repository to propose a change safely."
    ),
    Outcome.UNSUPPORTED_REQUEST: (
        "This request is not a change DevPilot can make by editing existing files in the "
        "repository."
    ),
}


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
    except InvestigationFailed as exc:
        proposal.model = exc.model
        proposal.tool_calls_used = exc.tool_calls_used
        proposal.investigation = summarise_activity(exc.activity)
        return _record_failure(session, proposal, exc)
    except AppError as exc:
        return _record_failure(session, proposal, exc)

    report = result.report
    proposal.summary = report.summary
    proposal.model = result.model
    proposal.tool_calls_used = result.tool_calls_used
    proposal.investigation = summarise_activity(result.activity)
    proposal.report = _build_report(result, repository)

    if result.patch is None:
        # The model investigated and concluded no change should be made. That
        # is an answer, not a crash — the report carries the explanation.
        proposal.status = ChangeStatus.FAILED
        proposal.error = report.downgraded_reason or _NO_CHANGE_MESSAGES.get(
            report.outcome, "No change could be derived from the repository evidence."
        )
        proposal.edits = []
        proposal.diff = ""
        session.add(proposal)
        session.commit()
        return proposal

    patch = result.patch
    proposal.edits = [
        {
            "path": edit["path"],
            "old_text": edit["old_text"],
            "new_text": edit["new_text"],
            "reason": str(edit.get("reason", "")),
        }
        for edit in (item for item in report.raw_changes if isinstance(item, dict))
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
    pull request. Those happen only when the person separately asks for the
    pull request, from ``approved``.
    """
    if proposal.status in (ChangeStatus.APPROVED, ChangeStatus.REJECTED):
        raise ConflictError(
            "This change has already been reviewed.",
            details={"status": str(proposal.status)},
        )
    if proposal.status != ChangeStatus.PROPOSED:
        raise ConflictError(
            "Only a proposed change can be reviewed.",
            details={"status": str(proposal.status)},
        )

    # Re-checked at review time, not only at generation: the repository can be
    # re-indexed while a proposal sits waiting.
    if approve:
        try:
            revalidate_against_snapshot(session, proposal=proposal, repository=repository)
        except StaleSnapshotError:
            proposal.status = ChangeStatus.STALE
            session.commit()
            raise

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
    if proposal.status not in (ChangeStatus.PROPOSED, ChangeStatus.APPROVED):
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


def _build_report(result: InvestigationResult, repository: Repository) -> dict[str, Any]:
    """The persisted, reviewable account of an investigation.

    Locations and the model's own claims only — never file contents, never the
    model's reasoning. ``files`` pins each changed file to the blob its anchors
    were validated against, which is what later staleness checks compare.
    """
    report = result.report
    patch = result.patch
    return {
        "outcome": report.outcome.value,
        "summary": report.summary,
        "root_cause": report.root_cause,
        "confidence": report.confidence.value,
        "expected_behavior": report.expected_behavior,
        "missing_information": report.missing_information,
        "downgraded_reason": report.downgraded_reason,
        "relevant_files": report.relevant_files,
        "evidence": report.evidence,
        "sources_consulted": result.evidence,
        "proposed_changes": [
            {
                "path": anchor.path,
                "start_line": anchor.start_line,
                "end_line": anchor.end_line,
                "reason": anchor.reason,
            }
            for file_patch in (patch.files if patch else [])
            for anchor in file_patch.anchors
        ],
        "files": [
            {
                "path": file_patch.path,
                "blob_sha": file_patch.blob_sha,
                "additions": file_patch.additions,
                "deletions": file_patch.deletions,
            }
            for file_patch in (patch.files if patch else [])
        ],
        "validation": {
            "anchors_matched_exactly_once": sum(len(f.anchors) for f in patch.files)
            if patch
            else 0,
            "diff_verified": patch is not None,
            "snapshot_commit_sha": repository.indexed_commit_sha,
            "unresolved_citations": report.unresolved_citations,
        },
        "investigation": {
            "steps": result.steps,
            "tool_calls": result.tool_calls_used,
            "tools_used": result.tools_used,
            "hit_tool_limit": result.hit_tool_limit,
            "repair_attempts": result.repair_attempts,
            "seed_sources": result.seed_sources,
            "files_read": sorted(result.files_read),
        },
    }


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
