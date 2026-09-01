"""Groq implementation of :class:`app.services.llm.provider.LLMProvider`.

Groq serves an OpenAI-compatible Chat Completions API
(``https://api.groq.com/openai/v1``), so this talks plain HTTP via ``httpx`` —
the same lightweight approach as the Voyage, GitHub, OpenRouter and Gemini
integrations, with no vendor SDK. Everything Groq-shaped is decoded here: the
OpenAI message / tool-call wire format, ``reasoning_effort`` / ``reasoning_format``
controls, ``response_format`` JSON-Schema structured output, SSE stream framing,
rate-limit headers and error translation. The rest of the application only ever
sees :class:`Message` / :class:`ToolCall` / :class:`StreamEvent` /
:class:`Completion`, exactly as it does for the other providers.

Model note (verified against Groq's live docs 2026-09-01): ``openai/gpt-oss-120b``
has a 131,072-token context and a 65,536-token max output, and Groq lists it as
supporting tool use, JSON Object Mode, JSON Schema Mode (strict) and reasoning.
Two platform constraints shape this client:

* Structured Outputs cannot be combined with tool use *or* streaming in one
  request. Both are surfaced as a typed :class:`LLMCapabilityError` before a
  request is sent rather than as a confusing 400.
* ``reasoning_format`` must be ``parsed`` or ``hidden`` (never ``raw``) when tool
  use or JSON mode is active. DevPilot always sends ``hidden`` — the application
  only ever receives the final answer / tool calls / proposal, never the model's
  reasoning tokens.
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

_RETRY_STATUSES = frozenset({429, 500, 502, 503})
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_MAX_SECONDS = 15.0
_JITTER_FRACTION = 0.25

# DevPilot's LLM_EFFORT vocabulary has five levels; Groq's GPT-OSS models accept
# `low` / `medium` / `high`. The mapping is deliberately conservative: the
# default `high` folds to `medium`, because on the free tier reasoning tokens
# count against a small TPM budget and `medium` is enough for grounded Q&A and
# the bounded investigation loop. Only an explicit `xhigh` / `max` asks for the
# top level.
_REASONING_EFFORT_MAP: dict[str, str] = {
    "low": "low",
    "medium": "medium",
    "high": "medium",
    "xhigh": "high",
    "max": "high",
}

# The rate-limit headers Groq returns on every response (per its docs). Captured
# after each call and exposed via `.rate_limits` so a smoke test / operator can
# read the real remaining quota — DevPilot never claims a limit is "unlimited".
_RATE_LIMIT_HEADERS = (
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-reset-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-tokens",
    "retry-after",
)

# 400 error `code` values that mean "the model produced something unusable",
# not "your request was too big". Kept distinct so validation stays strict.
_MALFORMED_OUTPUT_CODES = frozenset(
    {"tool_use_failed", "json_validate_failed", "failed_generation"}
)


class GroqLLMProvider:
    """Generative model access via Groq's OpenAI-compatible API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "openai/gpt-oss-120b",
        api_url: str = "https://api.groq.com/openai/v1/chat/completions",
        max_output_tokens: int = 8_000,
        effort: str = "high",
        timeout_seconds: float = 120.0,
        max_retry_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._model = model
        self._api_url = api_url.rstrip("/")
        # The models catalogue lives one path segment up from chat/completions.
        self._models_url = self._api_url.rsplit("/chat/completions", 1)[0] + "/models"
        self._max_output_tokens = max_output_tokens
        self._reasoning_effort = _REASONING_EFFORT_MAP.get(effort)
        self._max_retry_seconds = max_retry_seconds
        # `transport` is a test seam, matching the other integrations: the suite
        # drives real request-building, SSE parsing and error translation
        # through it without a network.
        self._client = httpx.Client(
            timeout=timeout_seconds,
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        # Probed at most once per instance, only when a request actually needs
        # it (tools or structured output). Optimistic: only a definitive 404
        # from the catalogue flips this, so an unreachable catalogue never
        # blocks an otherwise-fine request.
        self._model_checked = False
        self._model_ok = True
        self._rate_limits: dict[str, str] = {}

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> dict[str, bool]:
        """What the configured model can do for this deployment.

        Tool use, JSON-Schema structured output and streaming are all listed
        capabilities of ``openai/gpt-oss-120b``. ``tools`` + ``structured_output``
        (and ``structured_output`` + streaming) cannot be combined in a single
        request; the agent loop never needs to.
        """
        return {"streaming": True, "tools": True, "structured_output": True}

    @property
    def rate_limits(self) -> dict[str, str]:
        """The rate-limit headers seen on the most recent Groq response.

        Empty until the first call. A copy, so a caller cannot mutate the
        provider's view.
        """
        return dict(self._rate_limits)

    def __enter__(self) -> GroqLLMProvider:
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

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] = (),
        max_tokens: int | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> Completion:
        self._ensure_model_usable(tools, response_schema)
        request = self._build_request(
            system, messages, tools, max_tokens, response_schema, stream=False
        )

        response = self._request_with_retry(lambda: self._client.post(self._api_url, json=request))
        self._capture_rate_limits(response.headers)
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

    def stream(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] = (),
        max_tokens: int | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> Iterator[StreamEvent]:
        if response_schema is not None:
            # Groq: "Streaming ... not currently supported with Structured
            # Outputs." Surface it before a request, not as a 400 mid-stream.
            raise LLMCapabilityError(
                "Groq cannot stream a structured-output response. Use complete() "
                "with response_schema instead.",
                details={"model": self._model},
            )
        self._ensure_model_usable(tools, None)
        request = self._build_request(system, messages, tools, max_tokens, None, stream=True)

        tool_calls_by_index: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        usage: dict[str, Any] = {}

        try:
            with self._client.stream("POST", self._api_url, json=request) as response:
                self._capture_rate_limits(response.headers)
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
        except GeneratorExit:
            # The consumer stopped early (e.g. the client disconnected). The
            # `with` block above has already closed the HTTP response, which is
            # how the upstream request is cancelled — nothing else to do.
            logger.info("Groq stream cancelled by the consumer")
            raise

        if finish_reason == "content_filter":
            raise LLMRefusalError("The model declined to answer this request.")

        yield StreamEvent(
            type=StreamEventType.DONE,
            tool_calls=self._finalize_tool_calls(tool_calls_by_index),
            stop_reason=finish_reason,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )

    # ---- capability detection --------------------------------------------

    def _ensure_model_usable(
        self, tools: Sequence[ToolDefinition], response_schema: dict[str, Any] | None
    ) -> None:
        """Fail clearly, before a request is sent, on a combination the model
        cannot serve or a model id that does not exist.

        Only runs for tool-calling / structured-output requests — a plain
        streamed answer never triggers the catalogue probe, keeping the free
        tier's small request budget for real work. The existence check mirrors
        the Gemini provider: it catches a typo'd ``GROQ_MODEL`` early, and fails
        *open* if the catalogue cannot be reached.
        """
        if tools and response_schema is not None:
            # Groq: "tool use ... not currently supported with Structured Outputs."
            raise LLMCapabilityError(
                "Groq cannot use tools and a response schema in the same request.",
                details={"model": self._model},
            )
        if not tools and response_schema is None:
            return

        if not self._model_checked:
            self._model_checked = True
            self._model_ok = self._probe_model_exists()
        if not self._model_ok:
            raise LLMCapabilityError(
                f"The configured Groq model '{self._model}' was not found. Set "
                "GROQ_MODEL to a current model id from https://console.groq.com/docs/models.",
                details={"model": self._model},
            )

    def _probe_model_exists(self) -> bool:
        """True unless the catalogue explicitly 404s the configured model."""
        try:
            response = self._client.get(f"{self._models_url}/{self._model}")
        except httpx.HTTPError:
            logger.warning("Could not reach the Groq model catalogue to check the model id")
            return True
        self._capture_rate_limits(response.headers)
        return response.status_code != 404

    # ---- request building -----------------------------------------------------

    def _build_request(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
        max_tokens: int | None,
        response_schema: dict[str, Any] | None,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self._model,
            "max_completion_tokens": max_tokens or self._max_output_tokens,
            "messages": [
                {"role": "system", "content": system},
                *self._encode_messages(messages),
            ],
            "stream": stream,
            # Never `raw`: DevPilot must not receive the model's reasoning
            # tokens, only the final answer / tool calls / proposal. `hidden`
            # is also the one format valid in every mode (tools, JSON, plain).
            "reasoning_format": "hidden",
        }
        if self._reasoning_effort:
            request["reasoning_effort"] = self._reasoning_effort
        if stream:
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
        if response_schema is not None:
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "devpilot_response",
                    "strict": True,
                    "schema": response_schema,
                },
            }
        return request

    @staticmethod
    def _encode_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
        """Translate normalized messages into OpenAI-shaped chat messages.

        Identical wire shape to the OpenRouter provider: a tool result is its
        own ``role: "tool"`` message, immediately after the assistant turn that
        asked for it; a tool-calling assistant turn carries ``tool_calls`` as a
        sibling of ``content``.
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
            arguments = GroqLLMProvider._parse_arguments(function.get("arguments"))
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
        """Tool arguments arrive as a JSON *string*, not a parsed object."""
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
        """Streamed tool calls arrive as fragments keyed by position, with
        argument text split across chunks — reassembly is concatenation by
        index, JSON-parsed only once the stream ends."""
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
                arguments=GroqLLMProvider._parse_arguments(entry["arguments"]),
            )
            for _, entry in sorted(by_index.items())
        ]

    @staticmethod
    def _parse_sse_line(line: str) -> str | None:
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

    # ---- rate-limit headers --------------------------------------------------

    def _capture_rate_limits(self, headers: httpx.Headers) -> None:
        seen = {name: headers[name] for name in _RATE_LIMIT_HEADERS if name in headers}
        if seen:
            self._rate_limits = seen

    # ---- retry -------------------------------------------------------------

    def _request_with_retry(self, send: Callable[[], httpx.Response]) -> httpx.Response:
        """Send, retrying rate limits and transient 5xx within a wall-clock budget.

        Honours Groq's ``Retry-After`` when present, falls back to bounded
        exponential backoff with jitter, and gives up once the budget is spent
        rather than retrying forever.
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
                last_error = exc
                last_response = None
            else:
                if response.is_success or response.status_code not in _RETRY_STATUSES:
                    return response
                last_response = response
                last_error = None
                self._capture_rate_limits(response.headers)

            if not self._sleep_before_retry(start, attempt, last_response):
                if last_response is not None:
                    return last_response
                raise self._translate_transport_error(
                    last_error or httpx.HTTPError("unreachable")
                )

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
        delay = min(hinted, remaining)
        logger.info(
            "Retrying Groq request in %.1fs (attempt %d, %.0fs budget left)",
            delay,
            attempt,
            remaining,
        )
        time.sleep(delay)
        return True

    # ---- error translation --------------------------------------------------

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Map a failed response to the DevPilot error taxonomy.

        The response body is logged, never returned: it can echo request
        content, which here includes repository source.
        """
        status_code = response.status_code
        code = self._error_code(response)

        if status_code in (401, 403):
            raise LLMUnauthorizedError("The model provider rejected the API key.")
        if status_code == 429:
            raise LLMRateLimitError("The model provider's rate limit was reached.")
        if status_code == 408:
            raise LLMTimeoutError("The model did not respond in time.")
        if status_code == 413:
            raise LLMContextTooLargeError(
                "The request was too large for the model. Try a narrower question."
            )
        if status_code == 400:
            if code in _MALFORMED_OUTPUT_CODES:
                logger.warning("Groq returned an unusable generation (code %s)", code)
                raise LLMResponseError(
                    "The model returned an unusable tool call or structured output."
                )
            logger.warning("Groq rejected the request (400, code %s)", code or "unknown")
            raise LLMContextTooLargeError(
                "The request was rejected by the model. It may be too large — try a "
                "narrower question."
            )
        if status_code == 404:
            raise LLMError("The configured Groq model was not found. Check GROQ_MODEL.")
        if status_code == 422:
            raise LLMResponseError("The model provider rejected the request payload.")
        logger.warning("Unexpected Groq status %s", status_code)
        raise LLMError(f"The model provider returned an unexpected status ({status_code}).")

    @staticmethod
    def _error_code(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return ""
        if not isinstance(body, dict):
            return ""
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("code") or "")
        return ""

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
