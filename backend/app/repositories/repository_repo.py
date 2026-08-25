"""Queries over the ``repositories`` table."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.repository import Repository


def list_repositories(
    session: Session, *, user_id: uuid.UUID | None = None, limit: int = 50, offset: int = 0
) -> list[Repository]:
    stmt = select(Repository).order_by(Repository.created_at.desc(), Repository.id)
    if user_id is not None:
        stmt = stmt.where(Repository.connected_by_user_id == user_id)
    return list(session.scalars(stmt.limit(limit).offset(offset)))


def count_repositories(session: Session, *, user_id: uuid.UUID | None = None) -> int:
    stmt = select(func.count()).select_from(Repository)
    if user_id is not None:
        stmt = stmt.where(Repository.connected_by_user_id == user_id)
    return session.scalar(stmt) or 0


def get_by_id(session: Session, repository_id: uuid.UUID) -> Repository | None:
    return session.get(Repository, repository_id)


def get_by_full_name(
    session: Session, *, owner: str, name: str, provider: str = "github"
) -> Repository | None:
    stmt = select(Repository).where(
        Repository.provider == provider,
        # Provider names are case-insensitive; the URL a user pastes may not
        # match the stored casing.
        func.lower(Repository.owner) == owner.lower(),
        func.lower(Repository.name) == name.lower(),
    )
    return session.scalars(stmt).one_or_none()
