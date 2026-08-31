"""Schemas for grounded question answering and change proposals."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.change import ChangeStatus
from app.models.conversation import MessageRole


def _reject_blank(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Must contain at least one non-whitespace character.")
    return cleaned


class AskRequest(BaseModel):
    """A question about the repository."""

    question: str = Field(min_length=1, max_length=4000)
    # Omitted on the first turn; supplied to continue an existing thread.
    conversation_id: uuid.UUID | None = None

    _validate = field_validator("question")(_reject_blank)


class SourceRead(BaseModel):
    """A citation, carrying everything needed to render it without a lookup."""

    model_config = ConfigDict(from_attributes=True)

    label: str
    chunk_id: uuid.UUID | None
    file_path: str
    symbol: str | None
    start_line: int
    end_line: int
    language: str | None
    score: float


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: MessageRole
    content: str
    model: str | None
    created_at: datetime
    sources: list[SourceRead] = Field(default_factory=list)


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    title: str | None
    created_at: datetime
    messages: list[MessageRead] = Field(default_factory=list)


class ChangeRequest(BaseModel):
    """A request to modify the repository."""

    request: str = Field(min_length=1, max_length=4000)
    conversation_id: uuid.UUID | None = None

    _validate = field_validator("request")(_reject_blank)


class ExecutionEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event: str
    at: str


class ChangeRead(BaseModel):
    """A proposal as the review UI sees it.

    ``edits`` is deliberately absent: the diff is what a human reviews, and the
    raw old/new text pairs are an implementation detail of validation.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    conversation_id: uuid.UUID | None
    status: ChangeStatus
    request: str
    summary: str
    diff: str
    files_changed: int
    tool_calls_used: int
    indexed_commit_sha: str | None
    model: str | None
    error: str | None
    investigation: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime
    reviewed_at: datetime | None

    # ---- execution: branch, commit, pull request ---------------------------
    branch_name: str | None
    commit_sha: str | None
    pr_number: int | None
    pr_url: str | None
    executed_at: datetime | None
    execution_error: str | None
    execution_events: list[ExecutionEventRead] = Field(default_factory=list)
