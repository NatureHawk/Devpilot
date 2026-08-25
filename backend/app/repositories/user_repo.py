"""Queries over the ``users`` table."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User


def get_by_id(session: Session, user_id: uuid.UUID) -> User | None:
    return session.get(User, user_id)


def get_by_github_id(session: Session, github_id: int) -> User | None:
    return session.scalars(select(User).where(User.github_id == github_id)).one_or_none()


def upsert_from_github(
    session: Session,
    *,
    github_id: int,
    login: str,
    display_name: str | None,
    email: str | None,
    avatar_url: str | None,
    token_encrypted: str,
    token_scopes: str,
) -> User:
    """Create or refresh the account behind a GitHub identity.

    Keyed on ``github_id`` rather than login, because a user can rename their
    account and would otherwise arrive as a stranger.
    """
    user = get_by_github_id(session, github_id)
    if user is None:
        user = User(github_id=github_id)
        session.add(user)

    user.github_login = login
    user.display_name = display_name
    user.email = email
    user.avatar_url = avatar_url
    user.github_token_encrypted = token_encrypted
    user.github_token_scopes = token_scopes

    session.flush([user])
    return user
