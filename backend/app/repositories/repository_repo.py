"""Queries over the ``repositories`` table."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.repository import Repository


def list_repositories(session: Session, *, limit: int = 50, offset: int = 0) -> list[Repository]:
    stmt = (
        select(Repository)
        .order_by(Repository.created_at.desc(), Repository.id)
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(stmt))


def count_repositories(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Repository)) or 0


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
