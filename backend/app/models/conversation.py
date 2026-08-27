from __future__ import annotations

import enum
import uuid
from typing import Any

from sqlalchemy import Enum, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey


class MessageRole(enum.StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class Conversation(Base, UUIDPrimaryKey, Timestamps):
    """A question-and-answer thread scoped to one repository.

    Conversations are meaningless without their repository, so the FK cascades.
    """

    __tablename__ = "conversations"
    __table_args__ = (
        # Sidebar and history views read the newest threads for one repository.
        Index("ix_conversations_repository_id_created_at", "repository_id", "created_at"),
    )

    repository_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str | None] = mapped_column(String(300))

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base, UUIDPrimaryKey, Timestamps):
    """One turn in a conversation."""

    __tablename__ = "messages"
    __table_args__ = (
        # Every read of a thread is "messages for this conversation, in order".
        Index("ix_messages_conversation_id_created_at", "conversation_id", "created_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, name="message_role", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Which model produced an assistant turn. Null for user turns. Recorded so a
    # thread stays interpretable after the configured model changes.
    model: Mapped[str | None] = mapped_column(String(100))
    # Retrieval facts for later evaluation: how many sources, how strong, how
    # long it took. Deliberately small and non-identifying — no source text, no
    # provider internals.
    retrieval_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    sources: Mapped[list[MessageSource]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="MessageSource.rank",
    )


class MessageSource(Base, UUIDPrimaryKey, Timestamps):
    """A citation: the chunk an assistant turn drew on.

    The chunk reference is nullable and the location is copied, so a citation
    still renders after a re-index replaces the chunk rows it pointed at. A
    conversation that becomes unreadable because the repository was re-indexed
    would defeat the point of citing sources at all.
    """

    __tablename__ = "message_sources"
    __table_args__ = (
        # Citations are always read for one message, in presentation order.
        Index("ix_message_sources_message_id_rank", "message_id", "rank"),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("code_chunks.id", ondelete="SET NULL")
    )

    # Position in the retrieval ranking, and what the label [S1] referred to.
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(10), nullable=False)

    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(300))
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    language: Mapped[str | None] = mapped_column(String(50))
    score: Mapped[float] = mapped_column(Float, nullable=False)

    message: Mapped[Message] = relationship(back_populates="sources")
