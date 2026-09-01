"""Groq provider: request shape, message mapping, streaming reassembly,
multi-round tool calling, tool-result mapping, structured output, reasoning
controls, error translation, rate-limit capture and bounded retry — driven
through a stub transport so the real wire-format code runs without a network.
Nothing here contacts Groq.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.integrations.groq_llm import GroqLLMProvider
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
    StreamEventType,
    ToolCall,
    ToolDefinition,
    ToolResult,
)

MODEL = "openai/gpt-oss-120b"
BASE = "https://api.groq.com/openai/v1/chat/completions"
CHAT_PATH = "/openai/v1/chat/completions"
MODEL_PATH = f"/openai/v1/models/{MODEL}"

_TOOL = ToolDefinition(
    name="search_code",
    description="Semantic code search",
    input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
)


def _completion_response(
    *,
    content: str = "hello",
    reasoning: str | None = None,
    tool_calls: list[dict[str, object]] | None = None,
    finish_reason: str = "stop",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    message: dict[str, object] = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning"] = reasoning
    if tool_calls:
        message["tool_calls"] = tool_calls
    return httpx.Response(
        200,
        json={
            "choices": [{"message": message, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
        headers=headers or {},
    )


def _sse(*chunks: dict[str, object], headers: dict[str, str] | None = None) -> httpx.Response:
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    return httpx.Response(
        200,
        content=body,
        headers={"content-type": "text/event-stream", **(headers or {})},
    )


def _model_ok(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"id": MODEL, "object": "model", "context_window": 131072})


def _tc_delta(*tool_calls: dict[str, object], finish: str | None = None) -> dict[str, object]:
    """One streaming choice carrying `tool_calls` delta fragments."""
    choice: dict[str, object] = {"delta": {"tool_calls": list(tool_calls)}}
    if finish is not None:
        choice["finish_reason"] = finish
    return {"choices": [choice]}


def _dispatch(
    *,
    on_post: Callable[[httpx.Request], httpx.Response],
    on_get: Callable[[httpx.Request], httpx.Response] = _model_ok,
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return on_get(request)
        return on_post(request)

    return handler


def _provider(
    handler: Callable[[httpx.Request], httpx.Response], **kwargs: object
) -> GroqLLMProvider:
    options: dict[str, object] = {"max_retry_seconds": 5.0, **kwargs}
    return GroqLLMProvider(
        api_key="test-key",
        model=MODEL,
        api_url=BASE,
        transport=httpx.MockTransport(handler),
        **options,  # type: ignore[arg-type]
    )


def _chat_only(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[[httpx.Request], httpx.Response]:
    def dispatch(request: httpx.Request) -> httpx.Response:
        assert request.url.path == CHAT_PATH, f"unexpected path {request.url.path}"
        return handler(request)

    return dispatch


def _body(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content)


# --------------------------------------------------------------------------- #
class TestRequestConstruction:
    def test_system_is_the_first_message_and_endpoint_is_groq(self) -> None:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(_body(request))
            assert str(request.url) == BASE
            return _completion_response()

        _provider(_chat_only(handler)).complete(system="be helpful", messages=[])

        assert seen[0]["messages"][0] == {"role": "system", "content": "be helpful"}
        assert seen[0]["model"] == MODEL

    def test_authorization_header_carries_a_bearer_key(self) -> None:
        seen: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("authorization"))
            return _completion_response()

        _provider(_chat_only(handler)).complete(system="s", messages=[])
        assert seen == ["Bearer test-key"]

    def test_reasoning_is_always_hidden_and_effort_is_mapped_conservatively(self) -> None:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(_body(request))
            return _completion_response()

        # default effort "high" folds to Groq "medium"
        _provider(_chat_only(handler)).complete(system="s", messages=[])
        assert seen[0]["reasoning_format"] == "hidden"
        assert seen[0]["reasoning_effort"] == "medium"

        # explicit "max" reaches the top level
        _provider(_chat_only(handler), effort="max").complete(system="s", messages=[])
        assert seen[1]["reasoning_effort"] == "high"

    def test_max_completion_tokens_is_used_not_max_tokens(self) -> None:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(_body(request))
            return _completion_response()

        _provider(_chat_only(handler)).complete(system="s", messages=[], max_tokens=1234)
        assert seen[0]["max_completion_tokens"] == 1234
        assert "max_tokens" not in seen[0]

    def test_tools_are_sent_in_openai_function_shape(self) -> None:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(_body(request))
            return _completion_response()

        _provider(_dispatch(on_post=handler)).complete(system="s", messages=[], tools=[_TOOL])

        assert seen[0]["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "search_code",
                    "description": "Semantic code search",
                    "parameters": _TOOL.input_schema,
                },
            }
        ]

    def test_streaming_requests_ask_for_usage(self) -> None:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(_body(request))
            return _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]})

        list(_provider(_chat_only(handler)).stream(system="s", messages=[]))
        assert seen[0]["stream_options"] == {"include_usage": True}
        assert seen[0]["stream"] is True


# --------------------------------------------------------------------------- #
class TestMessageEncoding:
    def test_tool_results_become_separate_tool_role_messages(self) -> None:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(_body(request))
            return _completion_response()

        messages = [
            Message(
                role=Role.USER,
                tool_results=[
                    ToolResult(tool_use_id="call_1", content='{"results": []}'),
                    ToolResult(tool_use_id="call_2", content="boom", is_error=True),
                ],
            )
        ]
        _provider(_chat_only(handler)).complete(system="s", messages=messages)

        assert seen[0]["messages"][1:] == [
            {"role": "tool", "tool_call_id": "call_1", "content": '{"results": []}'},
            {"role": "tool", "tool_call_id": "call_2", "content": "boom"},
        ]

    def test_assistant_tool_calls_carry_json_string_arguments(self) -> None:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(_body(request))
            return _completion_response()

        messages = [
            Message(
                role=Role.ASSISTANT,
                text="Looking that up.",
                tool_calls=[ToolCall(id="call_1", name="search_code", arguments={"query": "auth"})],
            )
        ]
        _provider(_chat_only(handler)).complete(system="s", messages=messages)

        encoded = seen[0]["messages"][1]
        assert encoded["role"] == "assistant"
        assert encoded["content"] == "Looking that up."
        assert encoded["tool_calls"][0]["function"]["arguments"] == '{"query": "auth"}'

    def test_plain_text_turns_encode_directly(self) -> None:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(_body(request))
            return _completion_response()

        _provider(_chat_only(handler)).complete(
            system="s", messages=[Message(role=Role.USER, text="hi")]
        )
        assert seen[0]["messages"][1] == {"role": "user", "content": "hi"}


# --------------------------------------------------------------------------- #
class TestNonStreamingCompletion:
    def test_parses_text_and_usage(self) -> None:
        provider = _provider(_chat_only(lambda r: _completion_response(content="the answer")))
        result = provider.complete(system="s", messages=[])

        assert result.text == "the answer"
        assert result.tool_calls == []
        assert result.stop_reason == "stop"
        assert (result.input_tokens, result.output_tokens) == (10, 5)

    def test_hidden_reasoning_field_is_never_surfaced(self) -> None:
        """Even if a `reasoning` field leaks into the payload, DevPilot only
        ever reads `content` — the hidden-reasoning boundary holds."""
        provider = _provider(
            _chat_only(
                lambda r: _completion_response(content="final answer", reasoning="secret chain")
            )
        )
        result = provider.complete(system="s", messages=[])
        assert result.text == "final answer"
        assert "secret" not in result.text

    def test_decodes_tool_calls_including_arguments(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _completion_response(
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "search_code", "arguments": '{"query": "auth"}'},
                    }
                ],
            )

        result = _provider(_dispatch(on_post=handler)).complete(
            system="s", messages=[], tools=[_TOOL]
        )

        assert result.wants_tools
        assert result.tool_calls == [
            ToolCall(id="call_1", name="search_code", arguments={"query": "auth"})
        ]

    def test_malformed_tool_arguments_raise_response_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _completion_response(
                content="",
                tool_calls=[
                    {"id": "call_1", "function": {"name": "search_code", "arguments": "not json"}}
                ],
            )

        with pytest.raises(LLMResponseError):
            _provider(_dispatch(on_post=handler)).complete(system="s", messages=[], tools=[_TOOL])

    def test_content_filter_finish_reason_is_a_refusal(self) -> None:
        provider = _provider(
            _chat_only(lambda r: _completion_response(content="", finish_reason="content_filter"))
        )
        with pytest.raises(LLMRefusalError):
            provider.complete(system="s", messages=[])


# --------------------------------------------------------------------------- #
class TestStreaming:
    def test_text_deltas_stream_incrementally_then_done(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _sse(
                {"choices": [{"delta": {"content": "Hel"}}]},
                {"choices": [{"delta": {"content": "lo"}}]},
                {
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                },
            )

        events = list(_provider(_chat_only(handler)).stream(system="s", messages=[]))

        text_events = [e for e in events if e.type is StreamEventType.TEXT]
        assert [e.text for e in text_events] == ["Hel", "lo"]
        done = events[-1]
        assert done.type is StreamEventType.DONE
        assert done.stop_reason == "stop"
        assert (done.input_tokens, done.output_tokens) == (3, 2)

    def test_tool_call_fragments_are_reassembled_across_chunks(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            opener = {"name": "search_code", "arguments": ""}
            return _sse(
                _tc_delta({"index": 0, "id": "call_1", "function": opener}),
                _tc_delta({"index": 0, "function": {"arguments": '{"qu'}}),
                _tc_delta(
                    {"index": 0, "function": {"arguments": 'ery": "auth"}'}}, finish="tool_calls"
                ),
            )

        events = list(
            _provider(_dispatch(on_post=handler)).stream(system="s", messages=[], tools=[_TOOL])
        )
        done = events[-1]
        assert done.type is StreamEventType.DONE
        assert done.tool_calls == [
            ToolCall(id="call_1", name="search_code", arguments={"query": "auth"})
        ]

    def test_multiple_tool_calls_reassemble_independently_by_index(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _sse(
                _tc_delta(
                    {"index": 0, "id": "c1", "function": {"name": "a", "arguments": "{}"}},
                    {"index": 1, "id": "c2", "function": {"name": "b", "arguments": "{}"}},
                    finish="tool_calls",
                ),
            )

        events = list(
            _provider(_dispatch(on_post=handler)).stream(system="s", messages=[], tools=[_TOOL])
        )
        assert [c.name for c in events[-1].tool_calls] == ["a", "b"]

    def test_blank_and_comment_lines_are_ignored(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = (
                ": ping\n\n"
                'data: {"choices": [{"delta": {"content": "hi"}}]}\n\n'
                "\n"
                'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(200, content=body)

        events = list(_provider(_chat_only(handler)).stream(system="s", messages=[]))
        text = "".join(e.text for e in events if e.type is StreamEventType.TEXT)
        assert text == "hi"

    def test_content_filter_mid_stream_is_a_refusal(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _sse({"choices": [{"delta": {}, "finish_reason": "content_filter"}]})

        with pytest.raises(LLMRefusalError):
            list(_provider(_chat_only(handler)).stream(system="s", messages=[]))


# --------------------------------------------------------------------------- #
class TestMultiRoundToolCalling:
    """Model -> tool call -> DevPilot result -> model continues -> final answer.

    The provider is the only thing that changes shape across rounds; the agent
    layer just replays normalized Messages. This drives two real `complete()`
    calls through one provider and asserts the second request carries the
    round-one tool call and its result in Groq's wire format.
    """

    def test_two_rounds_then_a_final_answer(self) -> None:
        requests: list[dict[str, Any]] = []
        round_no = {"n": 0}

        def on_post(request: httpx.Request) -> httpx.Response:
            requests.append(_body(request))
            round_no["n"] += 1
            if round_no["n"] == 1:
                return _completion_response(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        {
                            "id": "call_a",
                            "type": "function",
                            "function": {
                                "name": "search_code",
                                "arguments": '{"query": "epsilon"}',
                            },
                        }
                    ],
                )
            return _completion_response(content="Found it in parser.py.", finish_reason="stop")

        provider = _provider(_dispatch(on_post=on_post))

        first = provider.complete(
            system="s", messages=[Message(role=Role.USER, text="q")], tools=[_TOOL]
        )
        assert first.wants_tools
        call = first.tool_calls[0]

        # The agent layer would build exactly these two messages from `first`
        # and the executed tool result.
        second = provider.complete(
            system="s",
            messages=[
                Message(role=Role.USER, text="q"),
                Message(role=Role.ASSISTANT, text="", tool_calls=[call]),
                Message(
                    role=Role.USER,
                    tool_results=[ToolResult(tool_use_id=call.id, content='{"hits": 3}')],
                ),
            ],
            tools=[_TOOL],
        )
        assert second.text == "Found it in parser.py."
        assert second.stop_reason == "stop"

        # Round-two wire format: assistant tool_call message, then a tool message.
        round_two = requests[1]["messages"]
        assert round_two[-2] == {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_a",
                    "type": "function",
                    "function": {"name": "search_code", "arguments": '{"query": "epsilon"}'},
                }
            ],
        }
        assert round_two[-1] == {
            "role": "tool",
            "tool_call_id": "call_a",
            "content": '{"hits": 3}',
        }


# --------------------------------------------------------------------------- #
class TestStructuredOutput:
    def test_response_schema_becomes_a_json_schema_response_format(self) -> None:
        seen: list[dict[str, Any]] = []
        schema = {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        }

        def on_post(request: httpx.Request) -> httpx.Response:
            seen.append(_body(request))
            return _completion_response(content='{"summary": "x"}')

        result = _provider(_dispatch(on_post=on_post)).complete(
            system="s", messages=[Message(role=Role.USER, text="q")], response_schema=schema
        )
        rf = seen[0]["response_format"]
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["strict"] is True
        assert rf["json_schema"]["schema"] == schema
        # parsing stays in the application: the provider returns raw text.
        assert result.text == '{"summary": "x"}'

    def test_schema_and_tools_together_is_a_capability_error(self) -> None:
        provider = _provider(_dispatch(on_post=lambda r: _completion_response()))
        with pytest.raises(LLMCapabilityError):
            provider.complete(
                system="s",
                messages=[Message(role=Role.USER, text="q")],
                tools=[_TOOL],
                response_schema={"type": "object"},
            )

    def test_streaming_a_schema_is_a_capability_error(self) -> None:
        provider = _provider(_dispatch(on_post=lambda r: _completion_response()))
        with pytest.raises(LLMCapabilityError):
            list(
                provider.stream(
                    system="s",
                    messages=[Message(role=Role.USER, text="q")],
                    response_schema={"type": "object"},
                )
            )

    def test_json_validate_failed_is_a_response_error_not_context_error(self) -> None:
        def on_post(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400, json={"error": {"code": "json_validate_failed", "message": "bad"}}
            )

        provider = _provider(_dispatch(on_post=on_post), max_retry_seconds=0.0)
        with pytest.raises(LLMResponseError):
            provider.complete(
                system="s",
                messages=[Message(role=Role.USER, text="q")],
                response_schema={"type": "object"},
            )


# --------------------------------------------------------------------------- #
class TestCapabilityDetection:
    def test_a_missing_model_id_is_a_capability_error(self) -> None:
        def on_get(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": {"message": "no such model"}})

        with pytest.raises(LLMCapabilityError) as exc:
            _provider(_dispatch(on_post=lambda r: _completion_response(), on_get=on_get)).complete(
                system="s", messages=[], tools=[_TOOL]
            )
        assert exc.value.code == "llm_capability_unsupported"

    def test_an_unreachable_catalogue_fails_open(self) -> None:
        def on_get(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no metadata")

        result = _provider(
            _dispatch(on_post=lambda r: _completion_response(content="ok"), on_get=on_get)
        ).complete(system="s", messages=[], tools=[_TOOL])
        assert result.text == "ok"

    def test_plain_requests_never_probe_the_catalogue(self) -> None:
        def on_get(request: httpx.Request) -> httpx.Response:
            raise AssertionError("the catalogue must not be queried for a plain request")

        _provider(_dispatch(on_post=lambda r: _completion_response(), on_get=on_get)).complete(
            system="s", messages=[]
        )

    def test_the_catalogue_is_probed_at_most_once(self) -> None:
        gets = {"n": 0}

        def on_get(request: httpx.Request) -> httpx.Response:
            gets["n"] += 1
            return _model_ok(request)

        provider = _provider(_dispatch(on_post=lambda r: _completion_response(), on_get=on_get))
        provider.complete(system="s", messages=[], tools=[_TOOL])
        provider.complete(system="s", messages=[], tools=[_TOOL])
        assert gets["n"] == 1

    def test_capabilities_are_reported(self) -> None:
        provider = _provider(_chat_only(lambda r: _completion_response()))
        assert provider.capabilities == {
            "streaming": True,
            "tools": True,
            "structured_output": True,
        }


# --------------------------------------------------------------------------- #
class TestErrorTranslation:
    @pytest.mark.parametrize(
        ("status_code", "error"),
        [
            (400, LLMContextTooLargeError),
            (401, LLMUnauthorizedError),
            (403, LLMUnauthorizedError),
            (404, LLMError),
            (408, LLMTimeoutError),
            (413, LLMContextTooLargeError),
            (422, LLMResponseError),
            (429, LLMRateLimitError),
            (500, LLMError),
        ],
    )
    def test_status_codes_map_to_typed_errors(self, status_code: int, error: type) -> None:
        provider = _provider(
            _chat_only(lambda r: httpx.Response(status_code, json={"error": {"message": "x"}})),
            max_retry_seconds=0.0,
        )
        with pytest.raises(error):
            provider.complete(system="s", messages=[])

    def test_malformed_tool_call_400_is_a_response_error(self) -> None:
        provider = _provider(
            _chat_only(
                lambda r: httpx.Response(
                    400, json={"error": {"code": "tool_use_failed", "message": "x"}}
                )
            ),
            max_retry_seconds=0.0,
        )
        with pytest.raises(LLMResponseError):
            provider.complete(system="s", messages=[])

    def test_error_body_never_appears_in_the_message(self) -> None:
        body = {"error": {"message": "key gsk_secret_abc is bad"}}
        provider = _provider(_chat_only(lambda r: httpx.Response(401, json=body)))
        with pytest.raises(LLMUnauthorizedError) as exc:
            provider.complete(system="s", messages=[])
        assert "gsk_secret_abc" not in str(exc.value)

    def test_malformed_response_body_is_rejected(self) -> None:
        provider = _provider(_chat_only(lambda r: httpx.Response(200, content=b"not json")))
        with pytest.raises(LLMResponseError):
            provider.complete(system="s", messages=[])

    def test_timeout_is_a_typed_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("timed out")

        provider = _provider(_chat_only(handler), max_retry_seconds=0.0)
        with pytest.raises(LLMTimeoutError):
            provider.complete(system="s", messages=[])


# --------------------------------------------------------------------------- #
class TestRateLimiting:
    def test_rate_limit_headers_are_captured_from_a_success(self) -> None:
        headers = {
            "x-ratelimit-limit-requests": "1000",
            "x-ratelimit-remaining-requests": "998",
            "x-ratelimit-limit-tokens": "8000",
            "x-ratelimit-remaining-tokens": "7010",
            "x-ratelimit-reset-tokens": "7.2s",
        }
        provider = _provider(_chat_only(lambda r: _completion_response(headers=headers)))
        assert provider.rate_limits == {}  # nothing seen yet
        provider.complete(system="s", messages=[])
        assert provider.rate_limits["x-ratelimit-remaining-requests"] == "998"
        assert provider.rate_limits["x-ratelimit-remaining-tokens"] == "7010"

    def test_rate_limit_headers_are_captured_from_a_stream(self) -> None:
        headers = {"x-ratelimit-remaining-tokens": "42"}
        provider = _provider(
            _chat_only(
                lambda r: _sse(
                    {"choices": [{"delta": {}, "finish_reason": "stop"}]}, headers=headers
                )
            )
        )
        list(provider.stream(system="s", messages=[]))
        assert provider.rate_limits["x-ratelimit-remaining-tokens"] == "42"


# --------------------------------------------------------------------------- #
class TestRetryHandling:
    def test_succeeds_after_a_transient_rate_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.integrations.groq_llm.client.time.sleep", lambda _: None)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, json={"error": {"code": "rate_limit_exceeded"}})
            return _completion_response(content="recovered")

        result = _provider(_chat_only(handler)).complete(system="s", messages=[])
        assert calls["n"] == 2
        assert result.text == "recovered"

    def test_retry_after_header_is_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr("app.integrations.groq_llm.client.time.sleep", sleeps.append)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, headers={"retry-after": "4"}, json={"error": {}})
            return _completion_response()

        _provider(_chat_only(handler), max_retry_seconds=30.0).complete(system="s", messages=[])
        assert sleeps == [4.0]

    def test_gives_up_after_the_budget_and_never_loops_forever(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.integrations.groq_llm.client.time.sleep", lambda _: None)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(429, json={"error": {"code": "rate_limit_exceeded"}})

        with pytest.raises(LLMRateLimitError):
            _provider(_chat_only(handler), max_retry_seconds=0.05).complete(system="s", messages=[])
        assert calls["n"] >= 1

    def test_transient_5xx_is_retried_then_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.integrations.groq_llm.client.time.sleep", lambda _: None)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(503, json={"error": {"message": "unavailable"}})
            return _completion_response(content="ok")

        result = _provider(_chat_only(handler)).complete(system="s", messages=[])
        assert (calls["n"], result.text) == (2, "ok")


# --------------------------------------------------------------------------- #
class TestProviderProtocol:
    def test_satisfies_the_llm_provider_protocol(self) -> None:
        from app.services.llm.provider import LLMProvider

        provider = _provider(_chat_only(lambda r: _completion_response()))
        assert isinstance(provider, LLMProvider)

    def test_completion_wants_tools_helper(self) -> None:
        assert Completion(text="", tool_calls=[ToolCall("i", "n", {})], stop_reason="x").wants_tools
        assert not Completion(text="hi", tool_calls=[], stop_reason="stop").wants_tools
