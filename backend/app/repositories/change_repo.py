"""Queries over proposed changes."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.change import ChangeStatus, ProposedChange


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


def count_by_status(session: Session, *, repository_id: uuid.UUID, status: ChangeStatus) -> int:
    from sqlalchemy import func

    stmt = (
        select(func.count())
        .select_from(ProposedChange)
        .where(ProposedChange.repository_id == repository_id, ProposedChange.status == status)
    )
    return session.scalar(stmt) or 0
