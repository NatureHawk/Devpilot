"""Repository-grounded question answering.

Orchestration only: retrieval, context building, generation and persistence each
live in their own module. This is the sequence that ties them together and the
one place that decides what a turn means.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.conversation import Conversation, MessageRole, MessageSource
from app.models.conversation import Message as MessageRow
from app.models.repository import Repository
from app.repositories import conversation_repo
from app.services import prompts
from app.services.context_builder import BuiltContext, build_context, build_messages
from app.services.llm import (
    LLMProvider,
    Message,
    Role,
    StreamEventType,
)
from app.services.llm import (
    get_provider as get_llm_provider,
)
from app.services.retrieval import RetrievalResult, RetrievalStrength, retrieve

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AskChunk:
    """One increment streamed to the caller."""

    type: str
    text: str = ""
    sources: list[dict[str, object]] = field(default_factory=list)
    conversation_id: str | None = None
    message_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


def ask(
    session: Session,
    *,
    repository: Repository,
    question: str,
    conversation: Conversation,
    settings: Settings,
    provider: LLMProvider | None = None,
) -> Iterator[AskChunk]:
    """Answer one question, streaming as the model writes.

    Sources are emitted before any text: the UI shows what evidence was found
    while generation is still running, which is both faster to read and honest
    about the order things actually happen in.

    The answer is persisted only after the stream completes. A partial answer is
    not a turn — replaying it later as if it were complete would misrepresent
    the conversation.
    """
    started = time.monotonic()
    llm = provider or get_llm_provider(settings)

    retrieval = retrieve(session, repository=repository, query=question, settings=settings)

    context = build_context(
        repository_full_name=f"{repository.owner}/{repository.name}",
        retrieval=retrieval,
        max_chars=settings.effective_context_max_chars,
        max_sources=settings.context_max_sources,
    )
    # Evidence that did not survive selection is not evidence the model has.
    strength = retrieval.strength if context.included else RetrievalStrength.NONE

    yield AskChunk(
        type="sources",
        sources=_serialise_sources(context),
        conversation_id=str(conversation.id),
    )

    history = _load_history(session, conversation, limit=settings.conversation_history_turns)
    messages = build_messages(
        question=question,
        context=context,
        history=history,
        evidence_note=prompts.evidence_guidance(strength),
    )

    answer_parts: list[str] = []
    input_tokens = output_tokens = 0

    for event in llm.stream(
        system=prompts.ASK_SYSTEM_PROMPT,
        messages=messages,
        max_tokens=settings.llm_max_output_tokens,
    ):
        if event.type is StreamEventType.TEXT:
            answer_parts.append(event.text)
            yield AskChunk(type="text", text=event.text)
        elif event.type is StreamEventType.DONE:
            input_tokens = event.input_tokens
            output_tokens = event.output_tokens

    answer = "".join(answer_parts)
    latency_ms = int((time.monotonic() - started) * 1000)

    message = _persist_turn(
        session,
        conversation=conversation,
        question=question,
        answer=answer,
        context=context,
        retrieval=retrieval,
        strength=strength,
        model=llm.model,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )

    # Evaluation hook: enough to judge retrieval and answer quality later
    # without storing source text or provider internals.
    logger.info(
        "Ask completed repository_id=%s conversation_id=%s sources=%d files=%d strength=%s "
        "model=%s latency_ms=%d answer_chars=%d",
        repository.id,
        conversation.id,
        len(context.included),
        retrieval.distinct_files,
        strength.value,
        llm.model,
        latency_ms,
        len(answer),
    )

    yield AskChunk(
        type="done",
        conversation_id=str(conversation.id),
        message_id=str(message.id),
    )


def _load_history(session: Session, conversation: Conversation, *, limit: int) -> list[Message]:
    """Recent turns, oldest first.

    Bounded deliberately: a long thread must not crowd out the retrieved code,
    which is the evidence the answer actually depends on. Only the text is
    replayed — prior sources were evidence for prior questions, and this turn
    retrieves its own.
    """
    if limit <= 0:
        return []

    rows = conversation_repo.recent_messages(session, conversation.id, limit=limit)
    return [
        Message(
            role=Role.USER if row.role is MessageRole.USER else Role.ASSISTANT,
            text=row.content,
        )
        for row in rows
    ]


def _persist_turn(
    session: Session,
    *,
    conversation: Conversation,
    question: str,
    answer: str,
    context: BuiltContext,
    retrieval: RetrievalResult,
    strength: RetrievalStrength,
    model: str,
    latency_ms: int,
    input_tokens: int,
    output_tokens: int,
) -> MessageRow:
    """Store the user turn, the assistant turn, and the citations."""
    session.add(
        MessageRow(conversation_id=conversation.id, role=MessageRole.USER, content=question)
    )

    assistant = MessageRow(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content=answer,
        model=model,
        retrieval_metadata={
            "sources": len(context.included),
            "distinct_files": len({source.file_path for source in context.included}),
            "strength": strength.value,
            "searched_chunks": retrieval.searched_chunks,
            "semantic_candidates": len(retrieval.semantic),
            "lexical_candidates": len(retrieval.lexical),
            "context_chars": context.source_chars,
            "context_budget": context.budget,
            "embedding_model": retrieval.model,
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "truncated_sources": context.truncated_count,
        },
    )
    session.add(assistant)
    session.flush()

    for index, source in enumerate(context.included, start=1):
        session.add(
            MessageSource(
                message_id=assistant.id,
                chunk_id=uuid.UUID(source.chunk_id),
                rank=index,
                label=f"S{index}",
                file_path=source.file_path,
                symbol=source.qualified_symbol,
                start_line=source.start_line,
                end_line=source.end_line,
                language=source.language,
                score=source.score,
            )
        )

    # A conversation's title is its opening question, trimmed. Set once.
    if conversation.title is None:
        conversation.title = question[:120]

    session.commit()
    return assistant


def _serialise_sources(context: BuiltContext) -> list[dict[str, object]]:
    return [
        {
            "label": f"S{index}",
            "chunk_id": source.chunk_id,
            "file_path": source.file_path,
            "symbol": source.qualified_symbol,
            "chunk_type": source.chunk_type,
            "language": source.language,
            "start_line": source.start_line,
            "end_line": source.end_line,
            "score": round(source.score, 4),
            "content": source.content,
        }
        for index, source in enumerate(context.included, start=1)
    ]
