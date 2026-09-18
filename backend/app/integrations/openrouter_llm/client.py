"""OpenRouter implementation of :class:`app.services.llm.provider.LLMProvider`.

OpenRouter exposes an OpenAI-compatible Chat Completions API in front of many
models, so this talks plain HTTP (via ``httpx``, matching every other
integration in this codebase) rather than pulling in a vendor SDK. Everything
provider-shaped — OpenAI's message/tool-call wire format, SSE parsing, error
translation — is decoded here so the rest of the application never sees an
OpenAI-shaped dict; it only ever sees :class:`Message`/:class:`ToolCall`/
:class:`Completion`, exactly as it does for the Anthropic provider.
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable, Iterator, Sequence
from types import TracebackType
from typing import Any

import httpx

from app.services.llm.provider import (
    Completion,
    LLMCapabilityError,
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

# OpenRouter's own unified reasoning control, distinct from Anthropic's
# adaptive thinking. DevPilot's LLM_EFFORT vocabulary has two levels
# OpenRouter's `reasoning.effort` does not ("xhigh", "max"); both fold into
# its ceiling rather than erroring, since asking for *more* than the top level
# is a reasonable way to spell "give me the top level."
_EFFORT_MAP: dict[str, str] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "max": "high",
}

# Public, unauthenticated endpoint; used only for the capability check below.
_MODELS_URL = "https://openrouter.ai/api/v1/models"

_RETRY_STATUSES = frozenset({429, 500, 502, 503})
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_MAX_SECONDS = 15.0
_JITTER_FRACTION = 0.25


class OpenRouterLLMProvider:
    """Generative model access via OpenRouter's OpenAI-compatible API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        api_url: str = "https://openrouter.ai/api/v1/chat/completions",
        max_output_tokens: int = 8_000,
        effort: str = "high",
        timeout_seconds: float = 120.0,
        max_retry_seconds: float = 30.0,
        models_url: str = _MODELS_URL,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._model = model
        self._api_url = api_url
        self._models_url = models_url
        self._max_output_tokens = max_output_tokens
        self._effort = effort
        self._max_retry_seconds = max_retry_seconds
        # `transport` is a test seam, matching the Voyage and GitHub clients:
        # the suite drives real request-building, SSE parsing and error
        # translation through it without a network.
        self._client = httpx.Client(
            timeout=timeout_seconds,
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                # OpenRouter asks for these to attribute usage; harmless if
                # ignored, useful for anyone debugging from OpenRouter's side.
                "HTTP-Referer": "https://github.com/NatureHawk/Devpilot",
                "X-Title": "DevPilot",
            },
        )
        # Probed at most once per instance, only if a tool-calling request is
        # actually made — a plain-text answer never needs it. Optimistic by
        # default: only an explicit "no" from the catalogue flips this, so an
        # unreachable catalogue or an unlisted model never blocks a request
        # that might be perfectly fine.
        self._tool_support_checked = False
        self._tool_support_ok = True

    @property
    def model(self) -> str:
        return self._model

    def __enter__(self) -> OpenRouterLLMProvider:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # ---- public API ---------------------------------------------------------

    def stream(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] = (),
        max_tokens: int | None = None,
    ) -> Iterator[StreamEvent]:
        self._require_tool_support(tools)
        request = self._build_request(system, messages, tools, max_tokens, stream=True)

        tool_calls_by_index: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        usage: dict[str, Any] = {}

        try:
            with self._client.stream("POST", self._api_url, json=request) as response:
                if not response.is_success:
                    response.read()
                    self._raise_for_status(response)

                for line in response.iter_lines():
                    event = self._parse_sse_line(line)
                    if event is None:
                        continue
                    if event == "[DONE]":
                        break
                    chunk = self._parse_json(event)

                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]

                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]

                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield StreamEvent(type=StreamEventType.TEXT, text=content)

                    for fragment in delta.get("tool_calls") or []:
                        self._accumulate_tool_call(tool_calls_by_index, fragment)
        except httpx.HTTPError as exc:
            raise self._translate_transport_error(exc) from exc

        if finish_reason == "content_filter":
            raise LLMRefusalError("The model declined to answer this request.")

        yield StreamEvent(
            type=StreamEventType.DONE,
            tool_calls=self._finalize_tool_calls(tool_calls_by_index),
            stop_reason=finish_reason,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] = (),
        max_tokens: int | None = None,
    ) -> Completion:
        self._require_tool_support(tools)
        request = self._build_request(system, messages, tools, max_tokens, stream=False)

        response = self._request_with_retry(lambda: self._client.post(self._api_url, json=request))
        if not response.is_success:
            self._raise_for_status(response)

        body = self._parse_json_response(response)
        choice = (body.get("choices") or [{}])[0]
        finish_reason = choice.get("finish_reason")

        if finish_reason == "content_filter":
            raise LLMRefusalError("The model declined to act on this request.")

        message = choice.get("message") or {}
        usage = body.get("usage") or {}

        return Completion(
            text=message.get("content") or "",
            tool_calls=self._decode_tool_calls(message.get("tool_calls") or []),
            stop_reason=finish_reason,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )

    # ---- capability detection ------------------------------------------------

    def _require_tool_support(self, tools: Sequence[ToolDefinition]) -> None:
        """Fail clearly, before a request is sent, if this model can't do
        what is being asked of it.

        Checked against OpenRouter's own public model catalogue rather than a
        hardcoded list — a free model's capabilities (and its very existence)
        can change without this codebase changing. Checked at most once per
        instance: a plain-text request never triggers it, and a tool-calling
        agent loop that calls `complete()` many times in a row only probes
        once. A catalogue that can't be reached, or a model this deployment
        isn't listed in, fails *open* (``self._tool_support`` stays ``None``)
        — the point is to catch a definite mismatch early, not to add a new
        way for a real request to be blocked by a flaky metadata call.
        """
        if not tools:
            return
        if not self._tool_support_checked:
            self._tool_support_checked = True
            if self._probe_tool_support() is False:
                self._tool_support_ok = False
        if not self._tool_support_ok:
            raise LLMCapabilityError(
                f"The configured model '{self._model}' does not support tool "
                "calling. Set OPENROUTER_MODEL to one that does.",
                details={"model": self._model, "capability": "tools"},
            )

    def _probe_tool_support(self) -> bool | None:
        """True/False if the catalogue answers definitively, None if unknown."""
        try:
            response = self._client.get(self._models_url)
            if not response.is_success:
                return None
            body = response.json()
        except (httpx.HTTPError, ValueError):
            logger.warning("Could not reach the model catalogue to check tool support")
            return None

        for entry in body.get("data") or []:
            if entry.get("id") == self._model:
                supported = entry.get("supported_parameters") or []
                return "tools" in supported
        return None

    # ---- request building -----------------------------------------------------

    def _build_request(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
        max_tokens: int | None,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens or self._max_output_tokens,
            "messages": [
                {"role": "system", "content": system},
                *self._encode_messages(messages),
            ],
            "stream": stream,
        }
        if stream:
            # Without this, usage is omitted from the stream entirely on some
            # providers routed through OpenRouter.
            request["stream_options"] = {"include_usage": True}
        if tools:
            request["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in tools
            ]
        effort = _EFFORT_MAP.get(self._effort)
        if effort:
            request["reasoning"] = {"effort": effort}
        return request

    @staticmethod
    def _encode_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
        """Translate our messages into OpenAI-shaped chat messages.

        Unlike Anthropic, tool results cannot live inside a user turn's
        content — the OpenAI wire format requires one ``role: "tool"`` message
        per result, immediately following the assistant turn that requested
        it. A tool-calling assistant turn is its own message too, with
        ``tool_calls`` as a sibling of ``content`` rather than blocks within it.
        """
        encoded: list[dict[str, Any]] = []
        for message in messages:
            for result in message.tool_results:
                encoded.append(
                    {
                        "role": "tool",
                        "tool_call_id": result.tool_use_id,
                        "content": result.content,
                    }
                )

            if message.tool_calls:
                encoded.append(
                    {
                        "role": "assistant",
                        "content": message.text or None,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.name,
                                    "arguments": json.dumps(call.arguments),
                                },
                            }
                            for call in message.tool_calls
                        ],
                    }
                )
            elif message.text:
                encoded.append({"role": message.role.value, "content": message.text})

        return encoded

    # ---- response decoding ------------------------------------------------

    @staticmethod
    def _decode_tool_calls(raw_calls: list[dict[str, Any]]) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for raw in raw_calls:
            function = raw.get("function") or {}
            arguments = OpenRouterLLMProvider._parse_arguments(function.get("arguments"))
            calls.append(
                ToolCall(
                    id=str(raw.get("id", "")),
                    name=str(function.get("name", "")),
                    arguments=arguments,
                )
            )
        return calls

    @staticmethod
    def _parse_arguments(raw: Any) -> dict[str, Any]:
        """Tool arguments arrive as a JSON *string*, not a parsed object —
        unlike Anthropic, where the SDK has already parsed them."""
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise LLMResponseError("The model returned malformed tool arguments.") from exc
        if not isinstance(parsed, dict):
            raise LLMResponseError("The model returned malformed tool arguments.")
        return parsed

    @staticmethod
    def _accumulate_tool_call(
        by_index: dict[int, dict[str, str]], fragment: dict[str, Any]
    ) -> None:
        """Streamed tool calls arrive as incremental fragments keyed by
        position, with argument text split across many chunks — reassembly is
        concatenation by index, finalised (and JSON-parsed) only once the
        stream ends."""
        index = fragment.get("index", 0)
        entry = by_index.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if fragment.get("id"):
            entry["id"] = fragment["id"]
        function = fragment.get("function") or {}
        if function.get("name"):
            entry["name"] = function["name"]
        if function.get("arguments"):
            entry["arguments"] += function["arguments"]

    @staticmethod
    def _finalize_tool_calls(by_index: dict[int, dict[str, str]]) -> list[ToolCall]:
        return [
            ToolCall(
                id=entry["id"],
                name=entry["name"],
                arguments=OpenRouterLLMProvider._parse_arguments(entry["arguments"]),
            )
            for _, entry in sorted(by_index.items())
        ]

    @staticmethod
    def _parse_sse_line(line: str) -> str | None:
        """One SSE line -> its data payload, or None for anything else.

        Blank lines separate events; ``: `` lines are keep-alive comments;
        anything without a ``data:`` prefix is not a payload this API sends.
        """
        if not line or line.startswith(":"):
            return None
        if not line.startswith("data:"):
            return None
        return line[len("data:") :].strip()

    @staticmethod
    def _parse_json(data: str) -> dict[str, Any]:
        try:
            parsed = json.loads(data)
        except ValueError as exc:
            raise LLMResponseError("The model provider returned a malformed response.") from exc
        if not isinstance(parsed, dict):
            raise LLMResponseError("The model provider returned an unexpected payload.")
        return parsed

    @staticmethod
    def _parse_json_response(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMResponseError("The model provider returned a malformed response.") from exc
        if not isinstance(body, dict):
            raise LLMResponseError("The model provider returned an unexpected payload.")
        return body

    # ---- retry ---------------------------------------------------------------

    def _request_with_retry(self, send: Callable[[], httpx.Response]) -> httpx.Response:
        """Send, retrying rate limits and transient 5xx within a wall-clock budget.

        Free OpenRouter models are rate limited aggressively, and a 429 on the
        first turn used to end an entire investigation outright — the other two
        providers have retried since they were written. Honours ``Retry-After``
        when present, falls back to bounded exponential backoff with jitter, and
        gives up rather than retrying forever.
        """
        start = time.monotonic()
        attempt = 0
        last_error: httpx.HTTPError | None = None
        last_response: httpx.Response | None = None
        while True:
            attempt += 1
            try:
                response = send()
            except httpx.HTTPError as exc:
                last_error, last_response = exc, None
            else:
                if response.is_success or response.status_code not in _RETRY_STATUSES:
                    return response
                last_response, last_error = response, None

            if not self._sleep_before_retry(start, attempt, last_response):
                if last_response is not None:
                    return last_response
                raise self._translate_transport_error(last_error or httpx.HTTPError("unreachable"))

    def _sleep_before_retry(
        self, start: float, attempt: int, response: httpx.Response | None
    ) -> bool:
        remaining = self._max_retry_seconds - (time.monotonic() - start)
        if remaining <= 0:
            return False
        hinted = _retry_after_seconds(response)
        if hinted is None:
            base = min(_BACKOFF_MAX_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** min(attempt - 1, 10)))
            hinted = max(0.0, base + base * random.uniform(-_JITTER_FRACTION, _JITTER_FRACTION))
        if hinted > remaining:
            # Waiting what is left would spend the budget and still be too early.
            logger.info(
                "OpenRouter asked for %.0fs, more than the %.0fs budget left; giving up now",
                hinted,
                remaining,
            )
            return False
        logger.info(
            "Retrying OpenRouter request in %.1fs (attempt %d, %.0fs budget left)",
            hinted,
            attempt,
            remaining,
        )
        time.sleep(hinted)
        return True

    # ---- error translation --------------------------------------------------

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        """Map a failed response to our error taxonomy.

        The response body is logged, never returned: it can echo request
        content, which here includes repository source.
        """
        status_code = response.status_code
        if status_code == 401:
            raise LLMUnauthorizedError("The model provider rejected the API key.")
        if status_code == 402:
            raise LLMUnauthorizedError(
                "The model provider account has insufficient credit or quota."
            )
        if status_code == 429:
            raise LLMRateLimitError("The model provider's rate limit was reached.")
        if status_code == 400:
            # The most common 400 in this application is an over-long request;
            # the context builder's budget is what normally prevents it.
            logger.warning("Model rejected the request (400)")
            raise LLMContextTooLargeError(
                "The request was too large for the model. Try a narrower question."
            )
        logger.warning("Unexpected model provider status %s", status_code)
        raise LLMError(f"The model provider returned an unexpected status ({status_code}).")

    @staticmethod
    def _translate_transport_error(exc: httpx.HTTPError) -> LLMError:
        if isinstance(exc, httpx.TimeoutException):
            return LLMTimeoutError("The model did not respond in time.")
        return LLMError("The model provider could not be reached.")


def _retry_after_seconds(response: httpx.Response | None) -> float | None:
    """Seconds to wait from a ``Retry-After`` header, or None."""
    if response is None:
        return None
    header = response.headers.get("retry-after")
    if header is None:
        return None
    try:
        seconds = float(header)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None
