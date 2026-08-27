"""The generative-model interface the application depends on.

Nothing outside this package knows which provider is in use. The types here are
ours, not a vendor's: a different provider is a new implementation of
:class:`LLMProvider`, not a change to every caller.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from fastapi import status

from app.core.errors import AppError


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """A tool offered to the model.

    ``input_schema`` is JSON Schema. Kept as a plain dict because that is what
    every provider expects and inventing a wrapper type would buy nothing.
    """

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A tool the model asked to run."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResult:
    """The outcome of running one tool, returned to the model.

    ``is_error`` is passed through rather than raising: the model handles a
    failed tool better when it is told, and dropping the result entirely breaks
    the call/result pairing the API requires.
    """

    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass(slots=True)
class Message:
    """One turn of conversation.

    ``tool_calls`` and ``tool_results`` are carried alongside text so a provider
    implementation can rebuild its own wire format without the caller knowing
    what that format is.
    """

    role: Role
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


class StreamEventType(StrEnum):
    TEXT = "text"
    TOOL_CALL = "tool_call"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """One increment of a streamed response."""

    type: StreamEventType
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class Completion:
    """A finished, non-streamed response."""

    text: str
    tool_calls: list[ToolCall]
    stop_reason: str | None
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


# ---- errors ---------------------------------------------------------------


class LLMError(AppError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "llm_error"


class LLMNotConfiguredError(LLMError):
    """No model credentials in this deployment."""

    status_code = status.HTTP_501_NOT_IMPLEMENTED
    code = "llm_not_configured"


class LLMUnauthorizedError(LLMError):
    code = "llm_unauthorized"


class LLMRateLimitError(LLMError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "llm_rate_limited"


class LLMTimeoutError(LLMError):
    status_code = status.HTTP_504_GATEWAY_TIMEOUT
    code = "llm_timeout"


class LLMContextTooLargeError(LLMError):
    """The assembled request exceeded what the model accepts."""

    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "llm_context_too_large"


class LLMResponseError(LLMError):
    """The model returned something unusable, e.g. malformed tool arguments."""

    code = "llm_invalid_response"


class LLMRefusalError(LLMError):
    """The provider's safety system declined the request."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "llm_refused"


@runtime_checkable
class LLMProvider(Protocol):
    """Generates text, optionally using tools."""

    @property
    def model(self) -> str: ...

    def stream(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] = (),
        max_tokens: int | None = None,
    ) -> Iterator[StreamEvent]:
        """Yield increments as the model produces them.

        Implementations must actually stream from the provider — never buffer a
        finished response and re-emit it in pieces.
        """
        ...

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] = (),
        max_tokens: int | None = None,
    ) -> Completion:
        """Run to completion. Used by the investigation loop, which needs the
        whole turn before it can decide what to do next."""
        ...
