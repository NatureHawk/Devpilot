"""Queries over proposed changes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.change import ChangeStatus, ProposedChange

# How long a proposal may sit in ``executing`` before it is considered
# abandoned rather than in flight. The only way to reach that state is a
# process crashing between the claim and the next state transition — every
# normal exit path (success or failure) moves out of it before returning —
# so this is a crash-recovery window, not a normal operating latency.
_STUCK_EXECUTION_MINUTES = 10


def get_by_id(
    session: Session, change_id: uuid.UUID, *, repository_id: uuid.UUID | None = None
) -> ProposedChange | None:
    stmt = select(ProposedChange).where(ProposedChange.id == change_id)
    if repository_id is not None:
        stmt = stmt.where(ProposedChange.repository_id == repository_id)
    return session.scalars(stmt).one_or_none()


def list_for_repository(
    session: Session, *, repository_id: uuid.UUID, limit: int = 50
) -> list[ProposedChange]:
    stmt = (
        select(ProposedChange)
        .where(ProposedChange.repository_id == repository_id)
        .order_by(ProposedChange.created_at.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def claim_for_execution(session: Session, change_id: uuid.UUID) -> ChangeStatus | None:
    """Atomically move a proposal into ``executing``, or lose the race.

    Uses ``SELECT ... FOR UPDATE`` rather than a bare ``UPDATE ... WHERE``: a
    concurrent second call blocks on the row lock until the first commits (or
    rolls back), rather than racing a separate read against a separate write.
    That is also what lets this return the state execution should *resume*
    from — ``approved`` for a fresh run, ``committed`` if only PR creation is
    left to retry — since an ``UPDATE ... RETURNING`` only ever returns the
    row's *new* values, not what it was claimed from.

    Returns ``None`` if the row does not exist or is not currently claimable
    (already executing, or in a state execution cannot start from at all).

    A proposal stuck in ``executing`` past :data:`_STUCK_EXECUTION_MINUTES` is
    treated as abandoned and reclaimed. If a commit and branch were already
    recorded it resumes as ``committed`` (only the pull request is left);
    otherwise as ``approved``. Redoing the commit/branch step is safe: execution
    adopts a branch or pull request a crashed attempt already created on GitHub
    rather than creating a second one (worst case, one extra unused git object).
    """
    proposal = session.execute(
        select(ProposedChange).where(ProposedChange.id == change_id).with_for_update()
    ).scalar_one_or_none()
    if proposal is None:
        session.rollback()
        return None

    previous_status = proposal.status
    stuck = (
        previous_status == ChangeStatus.EXECUTING
        and proposal.updated_at is not None
        and proposal.updated_at < datetime.now(UTC) - timedelta(minutes=_STUCK_EXECUTION_MINUTES)
    )
    claimable = previous_status in (ChangeStatus.APPROVED, ChangeStatus.COMMITTED) or stuck
    if not claimable:
        session.rollback()
        return None

    proposal.status = ChangeStatus.EXECUTING
    session.commit()
    if stuck:
        committed = proposal.commit_sha is not None and proposal.branch_name is not None
        return ChangeStatus.COMMITTED if committed else ChangeStatus.APPROVED
    return previous_status


def record_event(proposal: ProposedChange, event: str) -> None:
    """Append to the audit trail in place. Caller still owns the commit."""
    proposal.execution_events = [
        *proposal.execution_events,
        {"event": event, "at": datetime.now(UTC).isoformat()},
    ]
