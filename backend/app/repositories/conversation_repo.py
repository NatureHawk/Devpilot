"""Queries over conversations, messages and their citations."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.conversation import Conversation, Message, MessageSource


def get_conversation(
    session: Session, conversation_id: uuid.UUID, *, repository_id: uuid.UUID
) -> Conversation | None:
    """Load a conversation, scoped to its repository.

    The repository filter is authorization, not convenience: a conversation id
    from another repository must not resolve just because it exists.
    """
    stmt = select(Conversation).where(
        Conversation.id == conversation_id, Conversation.repository_id == repository_id
    )
    return session.scalars(stmt).one_or_none()


def create_conversation(
    session: Session, *, repository_id: uuid.UUID, user_id: uuid.UUID | None
) -> Conversation:
    conversation = Conversation(repository_id=repository_id, user_id=user_id)
    session.add(conversation)
    session.flush()
    return conversation


def recent_messages(session: Session, conversation_id: uuid.UUID, *, limit: int) -> list[Message]:
    """The most recent turns, returned oldest-first.

    Selecting newest-first and reversing keeps the query on the index while
    still handing back chronological order for replay.
    """
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )
    return list(reversed(list(session.scalars(stmt))))


def list_messages(session: Session, conversation_id: uuid.UUID) -> list[Message]:
    """Every turn in a thread, with citations eagerly loaded."""
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .options(selectinload(Message.sources))
        .order_by(Message.created_at, Message.id)
    )
    return list(session.scalars(stmt))


def list_conversations(
    session: Session, *, repository_id: uuid.UUID, limit: int = 30
) -> list[Conversation]:
    stmt = (
        select(Conversation)
        .where(Conversation.repository_id == repository_id)
        .order_by(Conversation.created_at.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def sources_for_message(session: Session, message_id: uuid.UUID) -> list[MessageSource]:
    stmt = (
        select(MessageSource)
        .where(MessageSource.message_id == message_id)
        .order_by(MessageSource.rank)
    )
    return list(session.scalars(stmt))
