"""Anthropic implementation of :class:`app.services.llm.provider.LLMProvider`.

Uses the official ``anthropic`` SDK. Everything provider-shaped — message
encoding, streaming events, error classes — is translated here so the rest of
the application never imports ``anthropic``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from typing import Any

import anthropic

from app.services.llm.provider import (
    Completion,
    LLMContextTooLargeError,
    LLMError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnauthorizedError,
    Message,
    StreamEvent,
    StreamEventType,
    ToolCall,
    ToolDefinition,
)

logger = logging.getLogger(__name__)

# Adaptive thinking lets the model decide how much reasoning a question needs.
# `budget_tokens` is rejected by this model family, and a fixed budget would be
# the wrong shape anyway: a one-line lookup and a cross-file investigation do
# not deserve the same allowance.
_THINKING: dict[str, Any] = {"type": "adaptive"}


class AnthropicLLMProvider:
    """Generative model access via the Anthropic Messages API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-opus-5",
        max_output_tokens: int = 8_000,
        effort: str = "high",
        timeout_seconds: float = 120.0,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._effort = effort
        # `client` is a test seam: the suite injects a stub so streaming, tool
        # decoding and error translation run without a network.
        self._client = client or anthropic.Anthropic(
            api_key=api_key, timeout=timeout_seconds, max_retries=2
        )

    @property
    def model(self) -> str:
        return self._model

    # ---- public API -------------------------------------------------------

    def stream(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] = (),
        max_tokens: int | None = None,
    ) -> Iterator[StreamEvent]:
        request = self._build_request(system, messages, tools, max_tokens)

        try:
            with self._client.messages.stream(**request) as stream:
                for text in stream.text_stream:
                    yield StreamEvent(type=StreamEventType.TEXT, text=text)

                final = stream.get_final_message()
        except anthropic.APIError as exc:
            raise self._translate(exc) from exc

        # A refusal arrives as a successful response, so it has to be checked
        # rather than caught.
        if final.stop_reason == "refusal":
            raise LLMRefusalError("The model declined to answer this request.")

        yield StreamEvent(
            type=StreamEventType.DONE,
            tool_calls=self._decode_tool_calls(final.content),
            stop_reason=final.stop_reason,
            input_tokens=final.usage.input_tokens,
            output_tokens=final.usage.output_tokens,
        )

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] = (),
        max_tokens: int | None = None,
    ) -> Completion:
        request = self._build_request(system, messages, tools, max_tokens)

        try:
            # Streaming even for a whole-turn read: the SDK refuses large
            # non-streaming requests, and this keeps one code path with the API.
            with self._client.messages.stream(**request) as stream:
                response = stream.get_final_message()
        except anthropic.APIError as exc:
            raise self._translate(exc) from exc

        if response.stop_reason == "refusal":
            raise LLMRefusalError("The model declined to act on this request.")

        text = "".join(block.text for block in response.content if block.type == "text")
        return Completion(
            text=text,
            tool_calls=self._decode_tool_calls(response.content),
            stop_reason=response.stop_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    # ---- request building -------------------------------------------------

    def _build_request(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
        max_tokens: int | None,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens or self._max_output_tokens,
            "system": system,
            "messages": [self._encode(message) for message in messages],
            "thinking": _THINKING,
            "output_config": {"effort": self._effort},
        }
        if tools:
            request["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in tools
            ]
        return request

    @staticmethod
    def _encode(message: Message) -> dict[str, Any]:
        """Translate one of our messages into the provider's content blocks."""
        blocks: list[dict[str, Any]] = []

        # Tool results must be their own user turn, and must come first in it.
        for result in message.tool_results:
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": result.tool_use_id,
                    "content": result.content,
                    **({"is_error": True} if result.is_error else {}),
                }
            )

        if message.text:
            blocks.append({"type": "text", "text": message.text})

        for call in message.tool_calls:
            blocks.append(
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
            )

        return {"role": message.role.value, "content": blocks}

    @staticmethod
    def _decode_tool_calls(content: Any) -> list[ToolCall]:
        """Read tool_use blocks off a finished message.

        Inputs arrive already parsed by the SDK; they are never string-matched.
        """
        calls: list[ToolCall] = []
        for block in content:
            if block.type != "tool_use":
                continue
            if not isinstance(block.input, dict):
                raise LLMResponseError("The model returned malformed tool arguments.")
            calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
        return calls

    # ---- error translation ------------------------------------------------

    @staticmethod
    def _translate(exc: anthropic.APIError) -> LLMError:
        """Map an SDK exception to our taxonomy.

        The provider's message is logged, never returned: it can echo request
        content, which here includes repository source.
        """
        if isinstance(exc, anthropic.APITimeoutError):
            return LLMTimeoutError("The model did not respond in time.")
        if isinstance(exc, anthropic.AuthenticationError):
            return LLMUnauthorizedError("The model provider rejected the API key.")
        if isinstance(exc, anthropic.RateLimitError):
            return LLMRateLimitError("The model provider's rate limit was reached.")
        if isinstance(exc, anthropic.BadRequestError):
            # The most common 400 in this application is an over-long request;
            # the context builder's budget is what normally prevents it.
            logger.warning("Model rejected the request: %s", type(exc).__name__)
            return LLMContextTooLargeError(
                "The request was too large for the model. Try a narrower question."
            )
        if isinstance(exc, anthropic.APIConnectionError):
            return LLMError("The model provider could not be reached.")

        logger.warning("Unexpected model provider error: %s", type(exc).__name__)
        return LLMError("The model provider returned an unexpected error.")
