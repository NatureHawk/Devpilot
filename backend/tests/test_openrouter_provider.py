"""OpenRouter provider: request shape, streaming reassembly, and error
translation — driven through a stub transport so the real wire-format code
runs without a network. Nothing here contacts OpenRouter.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.integrations.openrouter_llm import OpenRouterLLMProvider
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

MODEL = "z-ai/glm-5.2:free"
CHAT_PATH = "/api/v1/chat/completions"
MODELS_PATH = "/api/v1/models"

_TOOL = ToolDefinition(name="search_code", description="search", input_schema={"type": "object"})


def _completion_response(
    *,
    content: str = "hello",
    tool_calls: list[dict[str, object]] | None = None,
    finish_reason: str = "stop",
) -> httpx.Response:
    message: dict[str, object] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return httpx.Response(
        200,
        json={
            "choices": [{"message": message, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )


def _sse(*chunks: dict[str, object]) -> httpx.Response:
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})


def _provider(
    handler: Callable[[httpx.Request], httpx.Response], **kwargs: object
) -> OpenRouterLLMProvider:
    return OpenRouterLLMProvider(
        api_key="test-key",
        model=MODEL,
        transport=httpx.MockTransport(handler),
        **kwargs,  # type: ignore[arg-type]
    )


def _chat_only(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[[httpx.Request], httpx.Response]:
    def dispatch(request: httpx.Request) -> httpx.Response:
        assert request.url.path == CHAT_PATH
        return handler(request)

    return dispatch


class TestRequestConstruction:
    def test_sends_system_as_first_message(self) -> None:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return _completion_response()

        _provider(_chat_only(handler)).complete(system="be helpful", messages=[])

        assert seen[0]["messages"][0] == {"role": "system", "content": "be helpful"}
        assert seen[0]["model"] == MODEL

    def test_authorization_header_carries_the_key(self) -> None:
        seen: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("authorization"))
            return _completion_response()

        _provider(_chat_only(handler)).complete(system="s", messages=[])
        assert seen == ["Bearer test-key"]

    def test_tools_are_sent_in_openai_function_shape(self) -> None:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if request.url.path == MODELS_PATH:
                return httpx.Response(200, json={"data": [_catalog_entry(MODEL, tools=True)]})
            seen.append(body)
            return _completion_response()

        _provider(handler).complete(system="s", messages=[], tools=[_TOOL])

        assert seen[0]["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "search_code",
                    "description": "search",
                    "parameters": {"type": "object"},
                },
            }
        ]

    def test_effort_maps_to_reasoning_effort(self) -> None:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return _completion_response()

        _provider(_chat_only(handler), effort="xhigh").complete(system="s", messages=[])
        # xhigh has no OpenRouter equivalent; it folds into the top level
        # rather than erroring, since "more than high" reasonably means high.
        assert seen[0]["reasoning"] == {"effort": "high"}


class TestMessageEncoding:
    def test_tool_results_become_separate_tool_role_messages(self) -> None:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return _completion_response()

        messages = [
            Message(
                role=Role.USER,
                tool_results=[
                    ToolResult(tool_use_id="call_1", content='{"results": []}'),
                    ToolResult(tool_use_id="call_2", content="error text", is_error=True),
                ],
            )
        ]
        _provider(_chat_only(handler)).complete(system="s", messages=messages)

        encoded = seen[0]["messages"][1:]
        assert encoded == [
            {"role": "tool", "tool_call_id": "call_1", "content": '{"results": []}'},
            {"role": "tool", "tool_call_id": "call_2", "content": "error text"},
        ]

    def test_assistant_tool_calls_carry_json_string_arguments(self) -> None:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
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
            seen.append(json.loads(request.content))
            return _completion_response()

        _provider(_chat_only(handler)).complete(
            system="s", messages=[Message(role=Role.USER, text="hi")]
        )
        assert seen[0]["messages"][1] == {"role": "user", "content": "hi"}


class TestNonStreamingCompletion:
    def test_parses_text_and_usage(self) -> None:
        provider = _provider(_chat_only(lambda r: _completion_response(content="the answer")))
        result = provider.complete(system="s", messages=[])

        assert result.text == "the answer"
        assert result.tool_calls == []
        assert result.stop_reason == "stop"
        assert result.input_tokens == 10
        assert result.output_tokens == 5

    def test_decodes_tool_calls_including_arguments(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == MODELS_PATH:
                return httpx.Response(200, json={"data": [_catalog_entry(MODEL, tools=True)]})
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

        result = _provider(handler).complete(system="s", messages=[], tools=[_TOOL])

        assert result.wants_tools
        assert result.tool_calls == [
            ToolCall(id="call_1", name="search_code", arguments={"query": "auth"})
        ]

    def test_malformed_tool_arguments_raise_response_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == MODELS_PATH:
                return httpx.Response(200, json={"data": [_catalog_entry(MODEL, tools=True)]})
            return _completion_response(
                content="",
                tool_calls=[
                    {
                        "id": "call_1",
                        "function": {"name": "search_code", "arguments": "not json"},
                    }
                ],
            )

        with pytest.raises(LLMResponseError):
            _provider(handler).complete(system="s", messages=[], tools=[_TOOL])

    def test_content_filter_finish_reason_is_a_refusal(self) -> None:
        provider = _provider(
            _chat_only(lambda r: _completion_response(content="", finish_reason="content_filter"))
        )
        with pytest.raises(LLMRefusalError):
            provider.complete(system="s", messages=[])


class TestStreaming:
    def test_text_deltas_stream_as_text_events(self) -> None:
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

        text_events = [e for e in events if e.type == StreamEventType.TEXT]
        assert "".join(e.text for e in text_events) == "Hello"
        done = events[-1]
        assert done.type == StreamEventType.DONE
        assert done.stop_reason == "stop"
        assert done.input_tokens == 3
        assert done.output_tokens == 2

    def test_tool_call_fragments_are_reassembled_across_chunks(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == MODELS_PATH:
                return httpx.Response(200, json={"data": [_catalog_entry(MODEL, tools=True)]})
            return _sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {"name": "search_code", "arguments": ""},
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"qu'}}]}}
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": 'ery": "auth"}'}}
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
            )

        events = list(_provider(handler).stream(system="s", messages=[], tools=[_TOOL]))
        done = events[-1]

        assert done.type == StreamEventType.DONE
        assert done.tool_calls == [
            ToolCall(id="call_1", name="search_code", arguments={"query": "auth"})
        ]

    def test_multiple_tool_calls_reassemble_independently_by_index(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == MODELS_PATH:
                return httpx.Response(200, json={"data": [_catalog_entry(MODEL, tools=True)]})
            return _sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {"name": "a", "arguments": "{}"},
                                    },
                                    {
                                        "index": 1,
                                        "id": "call_2",
                                        "function": {"name": "b", "arguments": "{}"},
                                    },
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
            )

        events = list(_provider(handler).stream(system="s", messages=[], tools=[_TOOL]))
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
        text = "".join(e.text for e in events if e.type == StreamEventType.TEXT)
        assert text == "hi"

    def test_content_filter_finish_reason_is_a_refusal_when_streamed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _sse({"choices": [{"delta": {}, "finish_reason": "content_filter"}]})

        with pytest.raises(LLMRefusalError):
            list(_provider(_chat_only(handler)).stream(system="s", messages=[]))


class TestErrorTranslation:
    @pytest.mark.parametrize(
        ("status_code", "error"),
        [
            (401, LLMUnauthorizedError),
            (402, LLMUnauthorizedError),
            (429, LLMRateLimitError),
            (400, LLMContextTooLargeError),
            (500, LLMError),
        ],
    )
    def test_status_codes_map_to_typed_errors(self, status_code: int, error: type) -> None:
        provider = _provider(_chat_only(lambda r: httpx.Response(status_code, json={})))
        with pytest.raises(error):
            provider.complete(system="s", messages=[])

    def test_error_body_never_appears_in_the_message(self) -> None:
        body = {"error": {"message": "key sk-secret-1"}}
        provider = _provider(_chat_only(lambda r: httpx.Response(401, json=body)))
        with pytest.raises(LLMUnauthorizedError) as exc:
            provider.complete(system="s", messages=[])
        assert "sk-secret-1" not in str(exc.value)

    def test_malformed_response_body_is_rejected(self) -> None:
        provider = _provider(_chat_only(lambda r: httpx.Response(200, content=b"not json")))
        with pytest.raises(LLMResponseError):
            provider.complete(system="s", messages=[])

    def test_timeout_is_a_typed_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("timed out")

        provider = _provider(_chat_only(handler))
        with pytest.raises(LLMTimeoutError):
            provider.complete(system="s", messages=[])


class TestCapabilityDetection:
    def test_a_model_without_tools_in_its_catalogue_entry_is_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == MODELS_PATH:
                return httpx.Response(200, json={"data": [_catalog_entry(MODEL, tools=False)]})
            raise AssertionError("a chat request should never be sent")

        with pytest.raises(LLMCapabilityError) as exc:
            _provider(handler).complete(system="s", messages=[], tools=[_TOOL])
        assert exc.value.code == "llm_capability_unsupported"

    def test_a_model_with_tools_proceeds_normally(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == MODELS_PATH:
                return httpx.Response(200, json={"data": [_catalog_entry(MODEL, tools=True)]})
            return _completion_response()

        result = _provider(handler).complete(system="s", messages=[], tools=[_TOOL])
        assert result.text == "hello"

    def test_an_unreachable_catalogue_fails_open(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == MODELS_PATH:
                return httpx.Response(503)
            return _completion_response()

        result = _provider(handler).complete(system="s", messages=[], tools=[_TOOL])
        assert result.text == "hello"

    def test_a_model_absent_from_the_catalogue_fails_open(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == MODELS_PATH:
                entry = _catalog_entry("some/other-model", tools=False)
                return httpx.Response(200, json={"data": [entry]})
            return _completion_response()

        result = _provider(handler).complete(system="s", messages=[], tools=[_TOOL])
        assert result.text == "hello"

    def test_no_tools_requested_never_queries_the_catalogue(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == MODELS_PATH:
                raise AssertionError("the catalogue should not be queried for a plain request")
            return _completion_response()

        _provider(handler).complete(system="s", messages=[])

    def test_the_catalogue_is_queried_at_most_once_per_instance(self) -> None:
        calls = {"models": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == MODELS_PATH:
                calls["models"] += 1
                return httpx.Response(200, json={"data": [_catalog_entry(MODEL, tools=True)]})
            return _completion_response()

        provider = _provider(handler)
        provider.complete(system="s", messages=[], tools=[_TOOL])
        provider.complete(system="s", messages=[], tools=[_TOOL])
        provider.complete(system="s", messages=[], tools=[_TOOL])

        assert calls["models"] == 1


def _catalog_entry(model_id: str, *, tools: bool) -> dict[str, object]:
    supported = ["max_tokens", "temperature"]
    if tools:
        supported.append("tools")
    return {"id": model_id, "supported_parameters": supported}
