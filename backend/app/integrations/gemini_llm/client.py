"""Gemini implementation of :class:`app.services.llm.provider.LLMProvider`.

Talks the Gemini API's classic ``v1beta`` ``generateContent`` /
``streamGenerateContent`` surface over plain ``httpx`` — the same lightweight
approach as the Voyage, GitHub, OpenRouter and Gemini-embedding integrations,
with no vendor SDK. Everything Gemini-shaped is decoded here: the
``systemInstruction`` / ``contents`` message format, ``functionDeclarations`` /
``functionCall`` / ``functionResponse`` tool wiring, ``responseSchema`` structured
output, SSE stream framing and error translation. The rest of the application
only ever sees :class:`Message` / :class:`ToolCall` / :class:`StreamEvent` /
:class:`Completion`, exactly as it does for the other two providers.

Model note: ``gemini-2.5-flash`` is retired for new API keys — Google's own 404
points to ``gemini-3.6-flash``, a stable (non-preview) model with a 1,048,576-
token context that supports streaming, function calling and native
``responseSchema`` structured output. Those three are platform guarantees for
any Gemini text model, so capability detection only has to confirm the
configured model exists and does ``generateContent``.
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
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
    Role,
    StreamEvent,
    StreamEventType,
    ToolCall,
    ToolDefinition,
)

logger = logging.getLogger(__name__)

_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_MAX_SECONDS = 15.0
_JITTER_FRACTION = 0.25

# finishReason / blockReason values that mean the safety system stopped the
# response. MAX_TOKENS is deliberately absent: a truncated answer is still an
# answer, and the caller decides what to do with it.
_REFUSAL_REASONS = frozenset(
    {"SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_SAFETY"}
)

# Gemini has no stable per-call id and, on 3.x, *requires* the opaque
# `thoughtSignature` from a functionCall part to be echoed back when that call
# is replayed in history ("missing thought_signature may lead to degraded model
# performance" — in practice a hard 400 with tools). Both are packed into the
# normalized ToolCall id, since that is the only field that survives the
# round trip to the matching ToolResult. The signature is base64
# (`A-Za-z0-9+/=`) and never contains the separator.
_ID_SEP = "::"


def _retry_delay_from(response: httpx.Response | None) -> float | None:
    """Seconds to wait, from ``Retry-After`` or a ``RetryInfo`` detail, or None."""
    if response is None:
        return None

    header = response.headers.get("retry-after")
    if header is not None:
        try:
            seconds = float(header)
        except ValueError:
            seconds = -1.0
        if seconds >= 0:
            return seconds

    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    for detail in (body.get("error") or {}).get("details") or []:
        if isinstance(detail, dict) and str(detail.get("@type", "")).endswith("RetryInfo"):
            raw = str(detail.get("retryDelay", "")).strip().rstrip("s")
            try:
                seconds = float(raw)
            except ValueError:
                return None
            return seconds if seconds >= 0 else None
    return None


class GeminiLLMProvider:
    """Generative model access via the Google Gemini API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-3.6-flash",
        api_url: str = "https://generativelanguage.googleapis.com/v1beta",
        max_output_tokens: int = 8_000,
        timeout_seconds: float = 120.0,
        max_retry_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._model = model
        self._base_url = api_url.rstrip("/")
        self._max_output_tokens = max_output_tokens
        self._max_retry_seconds = max_retry_seconds
        # `transport` is a test seam, matching the other integrations: the suite
        # drives real request-building, SSE parsing and error translation
        # through it without a network.
        self._client = httpx.Client(
            timeout=timeout_seconds,
            transport=transport,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        )
        # Probed at most once per instance, only when a request actually needs
        # it. Optimistic: only a definitive "no" from the catalogue flips this,
        # so an unreachable catalogue never blocks an otherwise-fine request.
        self._capability_checked = False
        self._model_ok = True

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> dict[str, bool]:
        """What the configured model can do for this deployment.

        Streaming, function calling and structured output are platform features
        of every Gemini text model, so once the model is confirmed to exist and
        support ``generateContent`` all three are available. ``tools`` and
        ``structured_output`` cannot be combined in a single request, which the
        agent loop never needs to do.
        """
        return {"streaming": True, "tools": True, "structured_output": True}

    def __enter__(self) -> GeminiLLMProvider:
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

    # ---- public API ------------------------------------------------------

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] = (),
        max_tokens: int | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> Completion:
        self._ensure_capable()
        payload = self._build_request(system, messages, tools, max_tokens, response_schema)
        url = f"{self._base_url}/models/{self._model}:generateContent"

        response = self._post(url, payload)
        body = self._parse_json(response)
        self._raise_if_blocked(body)

        candidate = (body.get("candidates") or [{}])[0]
        text, tool_calls = self._decode_candidate(candidate)
        usage = body.get("usageMetadata") or {}
        return Completion(
            text=text,
            tool_calls=tool_calls,
            stop_reason=candidate.get("finishReason"),
            input_tokens=int(usage.get("promptTokenCount") or 0),
            output_tokens=int(usage.get("candidatesTokenCount") or 0),
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
        self._ensure_capable()
        payload = self._build_request(system, messages, tools, max_tokens, response_schema)
        url = f"{self._base_url}/models/{self._model}:streamGenerateContent?alt=sse"

        tool_calls: list[ToolCall] = []
        finish_reason: str | None = None
        usage: dict[str, Any] = {}
        blocked: str | None = None

        try:
            with self._open_stream(url, payload) as response:
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = self._parse_chunk(line[len("data:") :].strip())
                    if chunk is None:
                        continue
                    if chunk.get("error"):
                        self._raise_for_error_body(chunk)
                    if chunk.get("usageMetadata"):
                        usage = chunk["usageMetadata"]
                    feedback = chunk.get("promptFeedback") or {}
                    if feedback.get("blockReason"):
                        blocked = str(feedback["blockReason"])
                    for candidate in chunk.get("candidates") or []:
                        if candidate.get("finishReason"):
                            finish_reason = candidate["finishReason"]
                        text, calls = self._decode_candidate(candidate)
                        if text:
                            yield StreamEvent(type=StreamEventType.TEXT, text=text)
                        tool_calls.extend(calls)
        except httpx.HTTPError as exc:
            raise self._translate_transport_error(exc) from exc

        if blocked or (finish_reason in _REFUSAL_REASONS):
            raise LLMRefusalError("The model declined to answer this request.")

        yield StreamEvent(
            type=StreamEventType.DONE,
            tool_calls=tool_calls,
            stop_reason=finish_reason,
            input_tokens=int(usage.get("promptTokenCount") or 0),
            output_tokens=int(usage.get("candidatesTokenCount") or 0),
        )

    # ---- capability detection ------------------------------------------

    def _ensure_capable(self) -> None:
        """Fail clearly, before a request is sent, if the model cannot generate.

        Checked against Google's own model catalogue rather than a hardcoded
        list. Checked at most once per instance. A catalogue that cannot be
        reached fails *open* — the point is to catch a definite mismatch (a
        typo'd or retired model id) early, not to add a new way for a real
        request to be blocked by a flaky metadata call.
        """
        if self._capability_checked:
            if not self._model_ok:
                raise LLMCapabilityError(
                    f"The configured Gemini model '{self._model}' cannot be used for text "
                    "generation. Set GEMINI_LLM_MODEL to a current generateContent model.",
                    details={"model": self._model},
                )
            return

        self._capability_checked = True
        try:
            response = self._client.get(f"{self._base_url}/models/{self._model}")
        except httpx.HTTPError:
            logger.warning("Could not reach the Gemini model catalogue to check capabilities")
            return
        if response.status_code == 404:
            self._model_ok = False
            self._ensure_capable()
            return
        if not response.is_success:
            return
        try:
            methods = response.json().get("supportedGenerationMethods") or []
        except ValueError:
            return
        if "generateContent" not in methods:
            self._model_ok = False
            self._ensure_capable()

    # ---- request building --------------------------------------------

    def _build_request(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
        max_tokens: int | None,
        response_schema: dict[str, Any] | None,
    ) -> dict[str, Any]:
        generation_config: dict[str, Any] = {
            "maxOutputTokens": max_tokens or self._max_output_tokens
        }
        request: dict[str, Any] = {
            "contents": self._encode_messages(messages),
            "generationConfig": generation_config,
        }
        if system:
            request["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            if response_schema is not None:
                # Gemini rejects tools + responseSchema in one call. The agent
                # never asks for both; surfacing it as a typed error beats a
                # confusing 400 from the API.
                raise LLMCapabilityError(
                    "Gemini cannot use tools and a response schema in the same request.",
                    details={"model": self._model},
                )
            request["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": _to_gemini_schema(tool.input_schema),
                        }
                        for tool in tools
                    ]
                }
            ]
            request["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
        elif response_schema is not None:
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseSchema"] = response_schema
        return request

    @staticmethod
    def _encode_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
        """Translate normalized messages into Gemini ``contents``.

        A tool-result turn becomes its own ``role: "user"`` content of
        ``functionResponse`` parts — Gemini pairs each one to the preceding
        ``functionCall`` by name and position, so no id has to survive the round
        trip. An assistant turn that asked for tools carries ``functionCall``
        parts alongside any text; every other turn is plain text.
        """
        contents: list[dict[str, Any]] = []
        for message in messages:
            if message.tool_results:
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": result.tool_use_id.partition(_ID_SEP)[0],
                                    "response": (
                                        {"error": result.content}
                                        if result.is_error
                                        else {"result": result.content}
                                    ),
                                }
                            }
                            for result in message.tool_results
                        ],
                    }
                )

            parts: list[dict[str, Any]] = []
            if message.text:
                parts.append({"text": message.text})
            for call in message.tool_calls:
                part: dict[str, Any] = {"functionCall": {"name": call.name, "args": call.arguments}}
                signature = call.id.split(_ID_SEP, 2)[2] if call.id.count(_ID_SEP) >= 2 else ""
                if signature:
                    part["thoughtSignature"] = signature
                parts.append(part)
            if parts:
                role = "model" if message.role is Role.ASSISTANT else "user"
                contents.append({"role": role, "parts": parts})

        return contents

    # ---- response decoding ------------------------------------------

    @staticmethod
    def _decode_candidate(candidate: dict[str, Any]) -> tuple[str, list[ToolCall]]:
        parts = (candidate.get("content") or {}).get("parts") or []
        text_fragments: list[str] = []
        calls: list[ToolCall] = []
        for index, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            if part.get("text"):
                text_fragments.append(part["text"])
            function_call = part.get("functionCall")
            if function_call:
                name = str(function_call.get("name", ""))
                args = function_call.get("args") or {}
                if not isinstance(args, dict):
                    raise LLMResponseError("The model returned malformed tool arguments.")
                raw_id = str(function_call.get("id") or index)
                signature = str(part.get("thoughtSignature") or "")
                calls.append(
                    ToolCall(
                        id=f"{name}{_ID_SEP}{raw_id}{_ID_SEP}{signature}",
                        name=name,
                        arguments=dict(args),
                    )
                )
        return "".join(text_fragments), calls

    def _raise_if_blocked(self, body: dict[str, Any]) -> None:
        block_reason = (body.get("promptFeedback") or {}).get("blockReason")
        candidate = (body.get("candidates") or [{}])[0]
        if block_reason or candidate.get("finishReason") in _REFUSAL_REASONS:
            raise LLMRefusalError("The model declined to act on this request.")

    # ---- transport --------------------------------------------------

    def _post(self, url: str, payload: dict[str, Any]) -> httpx.Response:
        response = self._request_with_retry(lambda: self._client.post(url, json=payload))
        if not response.is_success:
            self._raise_for_status(response)
        return response

    @contextmanager
    def _open_stream(self, url: str, payload: dict[str, Any]) -> Iterator[httpx.Response]:
        """A streaming response, with the initial connection retried on a
        rate limit / transient 5xx within the same wall-clock budget."""
        start = time.monotonic()
        attempt = 0
        while True:
            attempt += 1
            with self._client.stream("POST", url, json=payload) as response:
                if response.is_success:
                    yield response
                    return
                response.read()
                if response.status_code not in _RETRY_STATUSES:
                    self._raise_for_status(response)
                if not self._sleep_before_retry(start, attempt, response):
                    self._raise_for_status(response)

    def _request_with_retry(self, send: Callable[[], httpx.Response]) -> httpx.Response:
        """Send, retrying rate limits and transient 5xx within a wall-clock budget.

        Honours the provider's own ``Retry-After`` / ``RetryInfo`` when present,
        falls back to bounded exponential backoff with jitter, and gives up once
        the budget is spent rather than retrying forever.
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

            if not self._sleep_before_retry(start, attempt, last_response):
                if last_response is not None:
                    return last_response
                raise self._translate_transport_error(last_error or httpx.HTTPError("unreachable"))

    def _sleep_before_retry(
        self, start: float, attempt: int, response: httpx.Response | None
    ) -> bool:
        """Sleep for the next backoff interval, or return False if out of budget."""
        remaining = self._max_retry_seconds - (time.monotonic() - start)
        if remaining <= 0:
            return False
        hinted = _retry_delay_from(response)
        if hinted is None:
            base = min(_BACKOFF_MAX_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** min(attempt - 1, 10)))
            hinted = max(0.0, base + base * random.uniform(-_JITTER_FRACTION, _JITTER_FRACTION))
        if hinted > remaining:
            # Gemini wants longer than this request may wait. Sleeping what is
            # left would spend the whole budget and still retry too early, so
            # report the limit now instead of stalling first.
            logger.info(
                "Gemini asked for %.0fs, more than the %.0fs budget left; giving up now",
                hinted,
                remaining,
            )
            return False
        delay = hinted
        logger.info(
            "Retrying Gemini request in %.1fs (attempt %d, %.0fs budget left)",
            delay,
            attempt,
            remaining,
        )
        time.sleep(delay)
        return True

    # ---- parsing / errors ----------------------------------------

    @staticmethod
    def _parse_json(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMResponseError("The model provider returned a malformed response.") from exc
        if not isinstance(body, dict):
            raise LLMResponseError("The model provider returned an unexpected payload.")
        return body

    @staticmethod
    def _parse_chunk(data: str) -> dict[str, Any] | None:
        if not data:
            return None
        try:
            parsed = json.loads(data)
        except ValueError as exc:
            raise LLMResponseError("The model provider returned a malformed stream chunk.") from exc
        return parsed if isinstance(parsed, dict) else None

    def _raise_for_error_body(self, chunk: dict[str, Any]) -> None:
        error = chunk.get("error") or {}
        code = int(error.get("code") or 0)
        if code == 429:
            raise LLMRateLimitError("The model provider's rate limit was reached.")
        if code in (401, 403):
            raise LLMUnauthorizedError("The model provider rejected the API key.")
        logger.warning("Gemini stream reported an error (code %s)", code)
        raise LLMError("The model provider returned an error mid-stream.")

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        """Map a failed response to the DevPilot error taxonomy.

        The response body is logged, never returned: it can echo request
        content, which here includes repository source.
        """
        status_code = response.status_code
        if status_code in (401, 403):
            raise LLMUnauthorizedError("The model provider rejected the API key.")
        if status_code == 429:
            raise LLMRateLimitError("The model provider's rate limit was reached.")
        if status_code == 400:
            # Gemini uses 400 both for "too big" and for a payload it cannot
            # parse. Telling a caller to narrow the question when the real
            # problem is the request shape sends them chasing the wrong thing,
            # so the two are separated by what the body actually says.
            message = _error_message(response)
            if "token" in message.lower():
                logger.warning("Gemini rejected the request as too large")
                raise LLMContextTooLargeError(
                    "The request was too large for the model. Try a narrower question."
                )
            logger.warning("Gemini rejected the request payload (400): %s", message[:300])
            raise LLMResponseError("The model provider rejected the request payload.")
        if status_code == 404:
            raise LLMError("The configured Gemini model was not found. Check GEMINI_LLM_MODEL.")
        logger.warning("Unexpected Gemini status %s", status_code)
        raise LLMError(f"The model provider returned an unexpected status ({status_code}).")

    @staticmethod
    def _translate_transport_error(exc: httpx.HTTPError) -> LLMError:
        if isinstance(exc, httpx.TimeoutException):
            return LLMTimeoutError("The model did not respond in time.")
        return LLMError("The model provider could not be reached.")


# ---- schema translation ------------------------------------------------------

# Gemini's `functionDeclarations.parameters` is an OpenAPI 3.0 Schema subset,
# not JSON Schema: it rejects the request outright on any field it does not
# know, `additionalProperties` among them. Tool schemas are written once, in
# JSON Schema, for every provider — so the translation belongs here rather than
# in the tool definitions, which must stay provider-neutral.
_GEMINI_SCHEMA_KEYS = frozenset(
    {
        "type",
        "format",
        "title",
        "description",
        "nullable",
        "enum",
        "maxItems",
        "minItems",
        "properties",
        "required",
        "items",
        "minimum",
        "maximum",
        "anyOf",
    }
)


def _to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Drop what Gemini does not accept, recursively, keeping meaning intact."""
    cleaned: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _GEMINI_SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            cleaned[key] = {
                name: _to_gemini_schema(item) if isinstance(item, dict) else item
                for name, item in value.items()
            }
        elif key == "items" and isinstance(value, dict):
            cleaned[key] = _to_gemini_schema(value)
        elif key == "anyOf" and isinstance(value, list):
            cleaned[key] = [
                _to_gemini_schema(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            cleaned[key] = value
    return cleaned


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    return str(error.get("message", "")) if isinstance(error, dict) else ""
