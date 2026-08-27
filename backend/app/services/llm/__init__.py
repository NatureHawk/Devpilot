"""Generative model access.

The provider is resolved here, so replacing Anthropic touches this module and
nothing else.
"""

from typing import TYPE_CHECKING

from app.services.llm.provider import (
    Completion,
    LLMContextTooLargeError,
    LLMError,
    LLMNotConfiguredError,
    LLMProvider,
    LLMRateLimitError,
    LLMRefusalError,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnauthorizedError,
    Message,
    Role,
    StreamEvent,
    StreamEventType,
    ToolCall,
    ToolDefinition,
    ToolResult,
)

if TYPE_CHECKING:
    from app.core.config import Settings

__all__ = [
    "Completion",
    "LLMContextTooLargeError",
    "LLMError",
    "LLMNotConfiguredError",
    "LLMProvider",
    "LLMRateLimitError",
    "LLMRefusalError",
    "LLMResponseError",
    "LLMTimeoutError",
    "LLMUnauthorizedError",
    "Message",
    "Role",
    "StreamEvent",
    "StreamEventType",
    "ToolCall",
    "ToolDefinition",
    "ToolResult",
    "get_provider",
]


def get_provider(settings: "Settings") -> LLMProvider:
    """Build the configured model provider.

    Raises rather than constructing a client that would fail on first use, so an
    unconfigured deployment reports a configuration state instead of an outage.
    """
    from app.integrations.anthropic_llm import AnthropicLLMProvider

    if not settings.llm_configured:
        raise LLMNotConfiguredError("No language model is configured for this deployment.")

    return AnthropicLLMProvider(
        api_key=settings.anthropic_api_key,
        model=settings.llm_model,
        max_output_tokens=settings.llm_max_output_tokens,
        effort=settings.llm_effort,
        timeout_seconds=settings.llm_timeout_seconds,
    )
