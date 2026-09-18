"""Gemini generative provider: request shape, message mapping, streaming,
tool-call round trips, structured output, error translation, rate-limit retry
and capability detection — driven through a stub transport so the real
wire-format code runs without a network. Nothing here contacts Google.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from unittest import mock

import httpx
import pytest

from app.integrations.gemini_llm import GeminiLLMProvider
from app.services.llm.provider import (
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
    StreamEventType,
    ToolCall,
    ToolDefinition,
    ToolResult,
)

MODEL = "gemini-3.6-flash"
BASE = "https://generativelanguage.googleapis.com/v1beta"
GEN_PATH = f"/v1beta/models/{MODEL}:generateContent"
STREAM_PATH = f"/v1beta/models/{MODEL}:streamGenerateContent"
CATALOGUE_PATH = f"/v1beta/models/{MODEL}"

_TOOL = ToolDefinition(
    name="search_code",
    description="Semantic code search",
    input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
)


def _catalogue_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "name": f"models/{MODEL}",
            "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
        },
    )


def _gen_response(
    *,
    text: str = "hello",
    parts: list[dict[str, object]] | None = None,
    finish_reason: str = "STOP",
    prompt_tokens: int = 11,
    output_tokens: int = 3,
    block_reason: str | None = None,
) -> httpx.Response:
    body: dict[str, object] = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": parts if parts is not None else [{"text": text}],
                },
                "finishReason": finish_reason,
                "index": 0,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": prompt_tokens,
            "candidatesTokenCount": output_tokens,
            "totalTokenCount": prompt_tokens + output_tokens,
        },
    }
    if block_reason is not None:
        body["promptFeedback"] = {"blockReason": block_reason}
        body["candidates"] = []
    return httpx.Response(200, json=body)


def _sse(*chunks: dict[str, object]) -> httpx.Response:
    payload = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
    return httpx.Response(200, content=payload, headers={"content-type": "text/event-stream"})


def _text_chunk(text: str, *, finish: str | None = None, usage: bool = False) -> dict[str, object]:
    candidate: dict[str, object] = {
        "content": {"role": "model", "parts": [{"text": text}]},
        "index": 0,
    }
    if finish is not None:
        candidate["finishReason"] = finish
    chunk: dict[str, object] = {"candidates": [candidate]}
    if usage:
        chunk["usageMetadata"] = {"promptTokenCount": 20, "candidatesTokenCount": 9}
    return chunk


def _dispatch(
    *,
    on_post: Callable[[httpx.Request], httpx.Response],
    on_get: Callable[[httpx.Request], httpx.Response] = _catalogue_ok,
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return on_get(request)
        return on_post(request)

    return handler


def _provider(
    handler: Callable[[httpx.Request], httpx.Response], **kwargs: object
) -> GeminiLLMProvider:
    options: dict[str, object] = {"max_retry_seconds": 5.0, **kwargs}
    return GeminiLLMProvider(
        api_key="test-key",
        model=MODEL,
        api_url=BASE,
        transport=httpx.MockTransport(handler),
        **options,  # type: ignore[arg-type]
    )


def _body(request: httpx.Request) -> dict[str, object]:
    return json.loads(request.content)


# --------------------------------------------------------------------------- #
class TestCapabilityDetection:
    def test_probes_the_catalogue_once_then_proceeds(self) -> None:
        gets = {"n": 0}

        def on_get(request: httpx.Request) -> httpx.Response:
            gets["n"] += 1
            return _catalogue_ok(request)

        provider = _provider(_dispatch(on_post=lambda r: _gen_response(), on_get=on_get))
        provider.complete(system="s", messages=[Message(role=Role.USER, text="hi")])
        provider.complete(system="s", messages=[Message(role=Role.USER, text="again")])

        assert gets["n"] == 1  # cached after the first check

    def test_a_missing_model_is_a_capability_error(self) -> None:
        provider = _provider(
            _dispatch(on_post=lambda r: _gen_response(), on_get=lambda r: httpx.Response(404))
        )
        with pytest.raises(LLMCapabilityError):
            provider.complete(system="s", messages=[Message(role=Role.USER, text="hi")])

    def test_a_model_without_generatecontent_is_a_capability_error(self) -> None:
        def on_get(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"supportedGenerationMethods": ["embedContent"]})

        provider = _provider(_dispatch(on_post=lambda r: _gen_response(), on_get=on_get))
        with pytest.raises(LLMCapabilityError):
            provider.complete(system="s", messages=[Message(role=Role.USER, text="hi")])

    def test_an_unreachable_catalogue_fails_open(self) -> None:
        def on_get(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no metadata")

        provider = _provider(_dispatch(on_post=lambda r: _gen_response(text="ok"), on_get=on_get))
        result = provider.complete(system="s", messages=[Message(role=Role.USER, text="hi")])
        assert result.text == "ok"

    def test_capabilities_are_reported(self) -> None:
        provider = _provider(_dispatch(on_post=lambda r: _gen_response()))
        assert provider.capabilities == {
            "streaming": True,
            "tools": True,
            "structured_output": True,
        }


# --------------------------------------------------------------------------- #
class TestRequestConstruction:
    def test_system_goes_to_system_instruction_not_contents(self) -> None:
        seen: dict[str, object] = {}

        def on_post(request: httpx.Request) -> httpx.Response:
            seen.update(_body(request))
            return _gen_response()

        _provider(_dispatch(on_post=on_post)).complete(
            system="You are DevPilot.", messages=[Message(role=Role.USER, text="hi")]
        )

        assert seen["systemInstruction"] == {"parts": [{"text": "You are DevPilot."}]}
        assert seen["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]

    def test_api_key_header_and_endpoint(self) -> None:
        seen: dict[str, object] = {}

        def on_post(request: httpx.Request) -> httpx.Response:
            seen["key"] = request.headers.get("x-goog-api-key")
            seen["url"] = str(request.url)
            return _gen_response()

        _provider(_dispatch(on_post=on_post)).complete(
            system="", messages=[Message(role=Role.USER, text="hi")]
        )
        assert seen["key"] == "test-key"
        assert str(seen["url"]).endswith(":generateContent")

    def test_assistant_role_maps_to_model(self) -> None:
        seen: dict[str, object] = {}

        def on_post(request: httpx.Request) -> httpx.Response:
            seen.update(_body(request))
            return _gen_response()

        _provider(_dispatch(on_post=on_post)).complete(
            system="",
            messages=[
                Message(role=Role.USER, text="q1"),
                Message(role=Role.ASSISTANT, text="a1"),
                Message(role=Role.USER, text="q2"),
            ],
        )
        roles = [c["role"] for c in seen["contents"]]  # type: ignore[index]
        assert roles == ["user", "model", "user"]

    def test_tools_become_function_declarations_with_auto_mode(self) -> None:
        seen: dict[str, object] = {}

        def on_post(request: httpx.Request) -> httpx.Response:
            seen.update(_body(request))
            return _gen_response()

        _provider(_dispatch(on_post=on_post)).complete(
            system="", messages=[Message(role=Role.USER, text="hi")], tools=[_TOOL]
        )
        decls = seen["tools"][0]["functionDeclarations"]  # type: ignore[index]
        assert decls[0]["name"] == "search_code"
        assert decls[0]["parameters"] == _TOOL.input_schema
        assert seen["toolConfig"] == {"functionCallingConfig": {"mode": "AUTO"}}

    def test_max_tokens_lands_in_generation_config(self) -> None:
        seen: dict[str, object] = {}

        def on_post(request: httpx.Request) -> httpx.Response:
            seen.update(_body(request))
            return _gen_response()

        _provider(_dispatch(on_post=on_post)).complete(
            system="", messages=[Message(role=Role.USER, text="hi")], max_tokens=1234
        )
        assert seen["generationConfig"]["maxOutputTokens"] == 1234  # type: ignore[index]


# --------------------------------------------------------------------------- #
class TestToolSchemaTranslation:
    """Gemini's functionDeclarations take an OpenAPI subset, not JSON Schema.

    An unknown field is a hard 400, so the real tool definitions — written once,
    provider-neutrally, in JSON Schema — must be translated on the way out.
    """

    def test_unsupported_json_schema_keywords_are_removed(self) -> None:
        from app.services.tools import TOOL_DEFINITIONS

        seen: dict[str, object] = {}

        def on_post(request: httpx.Request) -> httpx.Response:
            seen.update(_body(request))
            return _gen_response()

        _provider(_dispatch(on_post=on_post)).complete(
            system="", messages=[Message(role=Role.USER, text="hi")], tools=TOOL_DEFINITIONS
        )

        declarations = seen["tools"][0]["functionDeclarations"]  # type: ignore[index]
        assert [item["name"] for item in declarations] == [t.name for t in TOOL_DEFINITIONS]
        for declaration in declarations:
            assert "additionalProperties" not in json.dumps(declaration)

    def test_the_schema_still_describes_the_tool(self) -> None:
        """Stripping must not lose what the model needs to call the tool."""
        from app.integrations.gemini_llm.client import _to_gemini_schema

        cleaned = _to_gemini_schema(
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repository-relative path."},
                    "start_line": {"type": "integer", "minimum": 1},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["path"],
                "additionalProperties": False,
                "$schema": "https://json-schema.org/draft/2020-12/schema",
            }
        )

        assert cleaned == {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Repository-relative path."},
                "start_line": {"type": "integer", "minimum": 1},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["path"],
        }

    def test_nested_objects_are_cleaned_too(self) -> None:
        from app.integrations.gemini_llm.client import _to_gemini_schema

        cleaned = _to_gemini_schema(
            {
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "object",
                        "properties": {"kind": {"type": "string"}},
                        "additionalProperties": False,
                    }
                },
            }
        )
        assert "additionalProperties" not in json.dumps(cleaned)
        assert cleaned["properties"]["filter"]["properties"] == {"kind": {"type": "string"}}


# --------------------------------------------------------------------------- #
class TestMessageEncoding:
    def test_tool_results_become_a_function_response_user_turn(self) -> None:
        seen: dict[str, object] = {}

        def on_post(request: httpx.Request) -> httpx.Response:
            seen.update(_body(request))
            return _gen_response()

        _provider(_dispatch(on_post=on_post)).complete(
            system="",
            messages=[
                Message(role=Role.USER, text="find sessions"),
                Message(
                    role=Role.ASSISTANT,
                    text="",
                    tool_calls=[
                        ToolCall(
                            id="search_code::call_1::SIG",
                            name="search_code",
                            arguments={"query": "x"},
                        )
                    ],
                ),
                Message(
                    role=Role.USER,
                    tool_results=[
                        ToolResult(tool_use_id="search_code::call_1::SIG", content="found it")
                    ],
                ),
            ],
            tools=[_TOOL],
        )
        contents = seen["contents"]  # type: ignore[assignment]
        # assistant functionCall part carries the thought signature back
        model_turn = contents[1]
        assert model_turn["role"] == "model"
        assert model_turn["parts"][0]["functionCall"] == {
            "name": "search_code",
            "args": {"query": "x"},
        }
        assert model_turn["parts"][0]["thoughtSignature"] == "SIG"
        # tool result -> its own user turn, keyed by function name
        fr_turn = contents[2]
        assert fr_turn["role"] == "user"
        assert fr_turn["parts"][0]["functionResponse"] == {
            "name": "search_code",
            "response": {"result": "found it"},
        }

    def test_an_error_result_is_encoded_as_an_error_response(self) -> None:
        seen: dict[str, object] = {}

        def on_post(request: httpx.Request) -> httpx.Response:
            seen.update(_body(request))
            return _gen_response()

        _provider(_dispatch(on_post=on_post)).complete(
            system="",
            messages=[
                Message(
                    role=Role.USER,
                    tool_results=[
                        ToolResult(
                            tool_use_id="read_file::0::", content="no such path", is_error=True
                        )
                    ],
                )
            ],
        )
        fr = seen["contents"][0]["parts"][0]["functionResponse"]  # type: ignore[index]
        assert fr == {"name": "read_file", "response": {"error": "no such path"}}


# --------------------------------------------------------------------------- #
class TestNonStreamingCompletion:
    def test_parses_text_and_token_usage(self) -> None:
        provider = _provider(
            _dispatch(
                on_post=lambda r: _gen_response(
                    text="the answer", prompt_tokens=40, output_tokens=8
                )
            )
        )
        result = provider.complete(system="", messages=[Message(role=Role.USER, text="q")])
        assert result.text == "the answer"
        assert (result.input_tokens, result.output_tokens) == (40, 8)
        assert result.stop_reason == "STOP"
        assert result.wants_tools is False

    def test_decodes_function_calls_with_synthesised_id_and_signature(self) -> None:
        parts = [
            {
                "functionCall": {"name": "search_code", "args": {"query": "auth"}, "id": "call_9"},
                "thoughtSignature": "ABC123",
            }
        ]
        provider = _provider(_dispatch(on_post=lambda r: _gen_response(parts=parts)))
        result = provider.complete(
            system="", messages=[Message(role=Role.USER, text="q")], tools=[_TOOL]
        )

        assert result.wants_tools is True
        call = result.tool_calls[0]
        assert call.name == "search_code"
        assert call.arguments == {"query": "auth"}
        assert call.id == "search_code::call_9::ABC123"

    def test_multiple_function_calls_keep_order_and_distinct_ids(self) -> None:
        parts = [
            {
                "functionCall": {"name": "search_code", "args": {"query": "a"}, "id": "c1"},
                "thoughtSignature": "S1",
            },
            {
                "functionCall": {"name": "find_symbol", "args": {"name": "b"}, "id": "c2"},
                "thoughtSignature": "S2",
            },
        ]
        provider = _provider(_dispatch(on_post=lambda r: _gen_response(parts=parts)))
        result = provider.complete(
            system="", messages=[Message(role=Role.USER, text="q")], tools=[_TOOL]
        )

        assert [c.name for c in result.tool_calls] == ["search_code", "find_symbol"]
        assert len({c.id for c in result.tool_calls}) == 2

    def test_malformed_tool_arguments_raise_a_response_error(self) -> None:
        parts = [{"functionCall": {"name": "search_code", "args": "not-an-object"}}]
        provider = _provider(_dispatch(on_post=lambda r: _gen_response(parts=parts)))
        with pytest.raises(LLMResponseError):
            provider.complete(
                system="", messages=[Message(role=Role.USER, text="q")], tools=[_TOOL]
            )

    def test_a_safety_finish_reason_is_a_refusal(self) -> None:
        provider = _provider(
            _dispatch(on_post=lambda r: _gen_response(finish_reason="SAFETY", parts=[]))
        )
        with pytest.raises(LLMRefusalError):
            provider.complete(system="", messages=[Message(role=Role.USER, text="q")])

    def test_a_prompt_block_reason_is_a_refusal(self) -> None:
        provider = _provider(
            _dispatch(on_post=lambda r: _gen_response(block_reason="PROHIBITED_CONTENT"))
        )
        with pytest.raises(LLMRefusalError):
            provider.complete(system="", messages=[Message(role=Role.USER, text="q")])

    def test_max_tokens_is_not_treated_as_an_error(self) -> None:
        provider = _provider(
            _dispatch(on_post=lambda r: _gen_response(text="partial", finish_reason="MAX_TOKENS"))
        )
        result = provider.complete(system="", messages=[Message(role=Role.USER, text="q")])
        assert result.text == "partial"
        assert result.stop_reason == "MAX_TOKENS"


# --------------------------------------------------------------------------- #
class TestStructuredOutput:
    def test_response_schema_sets_mime_type_and_schema(self) -> None:
        seen: dict[str, object] = {}
        schema = {"type": "object", "properties": {"summary": {"type": "string"}}}

        def on_post(request: httpx.Request) -> httpx.Response:
            seen.update(_body(request))
            return _gen_response(text='{"summary": "x"}')

        result = _provider(_dispatch(on_post=on_post)).complete(
            system="", messages=[Message(role=Role.USER, text="q")], response_schema=schema
        )
        gc = seen["generationConfig"]  # type: ignore[assignment]
        assert gc["responseMimeType"] == "application/json"
        assert gc["responseSchema"] == schema
        # parsing stays in the application: the provider returns the raw text
        assert result.text == '{"summary": "x"}'

    def test_schema_and_tools_together_is_a_capability_error(self) -> None:
        provider = _provider(_dispatch(on_post=lambda r: _gen_response()))
        with pytest.raises(LLMCapabilityError):
            provider.complete(
                system="",
                messages=[Message(role=Role.USER, text="q")],
                tools=[_TOOL],
                response_schema={"type": "object"},
            )


# --------------------------------------------------------------------------- #
class TestStreaming:
    def test_text_deltas_arrive_incrementally_then_done(self) -> None:
        provider = _provider(
            _dispatch(
                on_post=lambda r: _sse(
                    _text_chunk("Hel"),
                    _text_chunk("lo "),
                    _text_chunk("world", finish="STOP", usage=True),
                )
            )
        )
        events = list(provider.stream(system="", messages=[Message(role=Role.USER, text="q")]))
        texts = [e.text for e in events if e.type is StreamEventType.TEXT]
        done = events[-1]
        assert texts == ["Hel", "lo ", "world"]
        assert done.type is StreamEventType.DONE
        assert done.stop_reason == "STOP"
        assert (done.input_tokens, done.output_tokens) == (20, 9)

    def test_blank_and_non_data_lines_are_ignored(self) -> None:
        body = (
            ": keep-alive\n\n"
            f"data: {json.dumps(_text_chunk('ok', finish='STOP', usage=True))}\n\n"
            "\n"
        )
        provider = _provider(
            _dispatch(
                on_post=lambda r: httpx.Response(
                    200, content=body, headers={"content-type": "text/event-stream"}
                )
            )
        )
        events = list(provider.stream(system="", messages=[Message(role=Role.USER, text="q")]))
        assert [e.text for e in events if e.type is StreamEventType.TEXT] == ["ok"]

    def test_a_streamed_function_call_reaches_the_done_event(self) -> None:
        fc_chunk = {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {
                                "functionCall": {
                                    "name": "search_code",
                                    "args": {"query": "z"},
                                    "id": "c1",
                                },
                                "thoughtSignature": "SIG",
                            }
                        ],
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2},
        }
        provider = _provider(_dispatch(on_post=lambda r: _sse(fc_chunk)))
        events = list(
            provider.stream(system="", messages=[Message(role=Role.USER, text="q")], tools=[_TOOL])
        )
        done = events[-1]
        assert done.type is StreamEventType.DONE
        assert done.tool_calls[0].name == "search_code"
        assert done.tool_calls[0].id == "search_code::c1::SIG"

    def test_a_safety_stop_mid_stream_is_a_refusal(self) -> None:
        provider = _provider(
            _dispatch(
                on_post=lambda r: _sse(
                    _text_chunk("part"), _text_chunk("", finish="SAFETY", usage=True)
                )
            )
        )
        with pytest.raises(LLMRefusalError):
            list(provider.stream(system="", messages=[Message(role=Role.USER, text="q")]))

    def test_an_error_object_in_a_chunk_is_translated(self) -> None:
        provider = _provider(
            _dispatch(on_post=lambda r: _sse({"error": {"code": 429, "message": "slow down"}}))
        )
        with pytest.raises(LLMRateLimitError):
            list(provider.stream(system="", messages=[Message(role=Role.USER, text="q")]))


# --------------------------------------------------------------------------- #
class TestErrorTranslation:
    @pytest.mark.parametrize(
        ("status_code", "error"),
        [
            (401, LLMUnauthorizedError),
            (403, LLMUnauthorizedError),
            (404, LLMError),
            (429, LLMRateLimitError),
            (500, LLMError),
        ],
    )
    def test_status_codes_map_to_typed_errors(self, status_code: int, error: type) -> None:
        provider = _provider(
            _dispatch(
                on_post=lambda r: httpx.Response(status_code, json={"error": {"message": "x"}})
            ),
            max_retry_seconds=0.0,
        )
        with pytest.raises(error):
            provider.complete(system="", messages=[Message(role=Role.USER, text="q")])

    def test_an_oversized_request_is_reported_as_too_large(self) -> None:
        provider = _provider(
            _dispatch(
                on_post=lambda r: httpx.Response(
                    400,
                    json={
                        "error": {"message": "The input token count (2000000) exceeds the maximum"}
                    },
                )
            ),
            max_retry_seconds=0.0,
        )
        with pytest.raises(LLMContextTooLargeError):
            provider.complete(system="", messages=[Message(role=Role.USER, text="q")])

    def test_a_rejected_payload_is_not_reported_as_too_large(self) -> None:
        """Gemini uses 400 for a payload it cannot parse as well as for size.
        Calling a malformed request "too large" sends the caller chasing the
        wrong problem."""
        provider = _provider(
            _dispatch(
                on_post=lambda r: httpx.Response(
                    400,
                    json={
                        "error": {
                            "message": (
                                'Invalid JSON payload received. Unknown name "additionalProperties"'
                            ),
                            "status": "INVALID_ARGUMENT",
                        }
                    },
                )
            ),
            max_retry_seconds=0.0,
        )
        with pytest.raises(LLMResponseError):
            provider.complete(system="", messages=[Message(role=Role.USER, text="q")])

    def test_error_body_never_appears_in_the_message(self) -> None:
        provider = _provider(
            _dispatch(
                on_post=lambda r: httpx.Response(
                    403, json={"error": {"message": "key AIza-secret-xyz is bad"}}
                )
            )
        )
        with pytest.raises(LLMUnauthorizedError) as exc:
            provider.complete(system="", messages=[Message(role=Role.USER, text="q")])
        assert "AIza-secret-xyz" not in str(exc.value)

    def test_malformed_json_body_is_a_response_error(self) -> None:
        provider = _provider(_dispatch(on_post=lambda r: httpx.Response(200, content=b"not json")))
        with pytest.raises(LLMResponseError):
            provider.complete(system="", messages=[Message(role=Role.USER, text="q")])

    def test_timeout_is_a_typed_error(self) -> None:
        def on_post(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow")

        provider = _provider(_dispatch(on_post=on_post), max_retry_seconds=0.0)
        with pytest.raises(LLMTimeoutError):
            provider.complete(system="", messages=[Message(role=Role.USER, text="q")])


# --------------------------------------------------------------------------- #
class TestRetryBudget:
    def test_a_retry_delay_longer_than_the_budget_fails_immediately(self) -> None:
        """Sleeping a hint the budget cannot cover spends the budget and still
        retries too early — the caller waits, then fails anyway."""
        attempts = 0
        slept: list[float] = []

        def on_post(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                429,
                json={
                    "error": {
                        "message": "quota",
                        "details": [
                            {
                                "@type": "type.googleapis.com/google.rpc.RetryInfo",
                                "retryDelay": "300s",
                            }
                        ],
                    }
                },
            )

        provider = _provider(_dispatch(on_post=on_post), max_retry_seconds=30.0)
        with mock.patch("time.sleep", slept.append), pytest.raises(LLMRateLimitError):
            provider.complete(system="", messages=[Message(role=Role.USER, text="q")])

        assert attempts == 1
        assert slept == []


# --------------------------------------------------------------------------- #
class TestRateLimitRetry:
    def test_succeeds_after_a_transient_rate_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.integrations.gemini_llm.client.time.sleep", lambda _: None)
        calls = {"n": 0}

        def on_post(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, json={"error": {"code": 429}})
            return _gen_response(text="recovered")

        result = _provider(_dispatch(on_post=on_post)).complete(
            system="", messages=[Message(role=Role.USER, text="q")]
        )
        assert calls["n"] == 2
        assert result.text == "recovered"

    def test_retry_after_header_is_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr("app.integrations.gemini_llm.client.time.sleep", sleeps.append)
        calls = {"n": 0}

        def on_post(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, headers={"Retry-After": "3"}, json={"error": {}})
            return _gen_response()

        _provider(_dispatch(on_post=on_post), max_retry_seconds=30.0).complete(
            system="", messages=[Message(role=Role.USER, text="q")]
        )
        assert sleeps == [3.0]

    def test_retry_info_delay_in_the_body_is_honoured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr("app.integrations.gemini_llm.client.time.sleep", sleeps.append)
        calls = {"n": 0}

        def on_post(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(
                    429,
                    json={
                        "error": {
                            "code": 429,
                            "details": [
                                {
                                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                                    "retryDelay": "7s",
                                }
                            ],
                        }
                    },
                )
            return _gen_response()

        _provider(_dispatch(on_post=on_post), max_retry_seconds=60.0).complete(
            system="", messages=[Message(role=Role.USER, text="q")]
        )
        assert sleeps == [7.0]

    def test_gives_up_after_the_budget_and_never_loops_forever(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.integrations.gemini_llm.client.time.sleep", lambda _: None)
        calls = {"n": 0}

        def on_post(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(429, json={"error": {"code": 429}})

        with pytest.raises(LLMRateLimitError):
            _provider(_dispatch(on_post=on_post), max_retry_seconds=0.05).complete(
                system="", messages=[Message(role=Role.USER, text="q")]
            )
        assert calls["n"] >= 1
