"""GitHub client behaviour, driven through httpx's mock transport.

Real client code runs — retries, error translation, tree walking — with only the
socket replaced.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable

import httpx
import pytest

from app.integrations.github.client import GitHubClient
from app.integrations.github.errors import (
    GitHubError,
    GitHubForbiddenError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubUnauthorizedError,
)

Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler, **kwargs: object) -> GitHubClient:
    return GitHubClient(
        token="test-token",
        base_url="https://api.github.com",
        transport=httpx.MockTransport(handler),
        **kwargs,  # type: ignore[arg-type]
    )


def json_response(
    payload: object, status: int = 200, headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(payload), headers=headers or {})


class TestTreeRetrieval:
    def test_recursive_tree_is_requested_and_parsed(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return json_response(
                {
                    "sha": "abc",
                    "truncated": False,
                    "tree": [
                        {"path": "a.py", "sha": "s1", "type": "blob", "size": 10},
                        {"path": "pkg", "sha": "s2", "type": "tree"},
                        {"path": "sub", "sha": "s3", "type": "commit"},
                    ],
                }
            )

        with make_client(handler) as client:
            tree = client.get_tree("o", "r", "abc")

        assert seen[0].url.params["recursive"] == "1"
        assert tree.truncated is False
        assert [entry.path for entry in tree.blobs] == ["a.py"]
        assert any(entry.is_tree for entry in tree.entries)
        assert any(entry.is_submodule for entry in tree.entries)

    def test_missing_size_is_none_not_zero(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return json_response(
                {"sha": "abc", "tree": [{"path": "a.py", "sha": "s1", "type": "blob"}]}
            )

        with make_client(handler) as client:
            assert client.get_tree("o", "r", "abc").blobs[0].size is None

    def test_truncation_is_reported_not_hidden(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return json_response({"sha": "abc", "truncated": True, "tree": []})

        with make_client(handler) as client:
            assert client.get_tree("o", "r", "abc").truncated is True


class TestTruncatedTreeFallback:
    """A truncated recursive tree must be completed, never quietly accepted."""

    @staticmethod
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("recursive") == "1":
            return json_response({"sha": "root", "truncated": True, "tree": []})

        sha = request.url.path.rsplit("/", 1)[-1]
        listings: dict[str, list[dict[str, object]]] = {
            "root": [
                {"path": "app", "sha": "d1", "type": "tree"},
                {"path": "top.py", "sha": "b1", "type": "blob", "size": 5},
            ],
            "d1": [
                {"path": "core", "sha": "d2", "type": "tree"},
                {"path": "main.py", "sha": "b2", "type": "blob", "size": 7},
            ],
            "d2": [{"path": "config.py", "sha": "b3", "type": "blob", "size": 9}],
        }
        return json_response({"sha": sha, "truncated": False, "tree": listings[sha]})

    def test_subtrees_are_walked_and_paths_rebuilt(self) -> None:
        with make_client(self.handler) as client:
            tree = client.get_full_tree("o", "r", "root")

        assert tree.truncated is False
        assert sorted(entry.path for entry in tree.blobs) == [
            "app/core/config.py",
            "app/main.py",
            "top.py",
        ]

    def test_untruncated_tree_costs_a_single_request(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return json_response(
                {
                    "sha": "root",
                    "truncated": False,
                    "tree": [{"path": "a.py", "sha": "b1", "type": "blob", "size": 1}],
                }
            )

        with make_client(handler) as client:
            client.get_full_tree("o", "r", "root")

        assert len(calls) == 1

    def test_a_tree_too_large_to_walk_fails_loudly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Better to refuse than to index part of a repository."""
        from app.integrations.github import client as client_module

        monkeypatch.setattr(client_module, "MAX_SUBTREE_REQUESTS", 1)

        with make_client(self.handler) as client, pytest.raises(GitHubError) as exc:
            client.get_full_tree("o", "r", "root")

        assert exc.value.details is not None
        assert exc.value.details["reason"] == "tree_truncated"


class TestBlobRetrieval:
    def test_base64_blob_is_decoded(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return json_response(
                {"encoding": "base64", "content": base64.b64encode(b"print(1)").decode()}
            )

        with make_client(handler) as client:
            assert client.get_blob("o", "r", "sha") == b"print(1)"

    def test_unsupported_encoding_is_rejected(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return json_response({"encoding": "quoted-printable", "content": "x"})

        with make_client(handler) as client, pytest.raises(GitHubError):
            client.get_blob("o", "r", "sha")

    def test_batch_reports_failures_per_blob_without_aborting(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("bad"):
                return json_response({"message": "Not Found"}, 404)
            return json_response(
                {"encoding": "base64", "content": base64.b64encode(b"ok").decode()}
            )

        with make_client(handler, max_concurrency=2) as client:
            results = dict(client.get_blobs("o", "r", ["good1", "bad", "good2"]))

        assert results["good1"] == b"ok"
        assert results["good2"] == b"ok"
        assert isinstance(results["bad"], GitHubNotFoundError)

    def test_empty_batch_makes_no_requests(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("no request expected")

        with make_client(handler) as client:
            assert list(client.get_blobs("o", "r", [])) == []


class TestErrorTranslation:
    @pytest.mark.parametrize(
        ("status", "headers", "expected"),
        [
            (401, {}, GitHubUnauthorizedError),
            (403, {}, GitHubForbiddenError),
            # A 403 with no quota left is rate limiting, not a permission problem.
            (403, {"x-ratelimit-remaining": "0"}, GitHubRateLimitError),
            (429, {}, GitHubRateLimitError),
            (404, {}, GitHubNotFoundError),
        ],
    )
    def test_status_codes_map_to_typed_errors(
        self, status: int, headers: dict[str, str], expected: type[Exception]
    ) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return json_response({"message": "nope"}, status, headers)

        with make_client(handler) as client, pytest.raises(expected):
            client.get_repository("o", "r")

    def test_error_body_is_not_leaked_to_the_caller(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return json_response({"message": "token ghp_secret is invalid"}, 401)

        with make_client(handler) as client, pytest.raises(GitHubUnauthorizedError) as exc:
            client.get_repository("o", "r")

        assert "ghp_secret" not in str(exc.value)

    def test_server_errors_are_retried_then_succeed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("time.sleep", lambda _: None)
        attempts: list[int] = []

        def handler(_: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) < 3:
                return json_response({"message": "boom"}, 503)
            return json_response({"id": 1, "name": "r", "owner": {"login": "o"}})

        with make_client(handler) as client:
            assert client.get_repository("o", "r").name == "r"

        assert len(attempts) == 3

    def test_client_errors_are_not_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("time.sleep", lambda _: None)
        attempts: list[int] = []

        def handler(_: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return json_response({"message": "nope"}, 404)

        with make_client(handler) as client, pytest.raises(GitHubNotFoundError):
            client.get_repository("o", "r")

        assert len(attempts) == 1


class TestRepositoryMetadata:
    def test_authorization_header_is_sent_when_a_token_is_present(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return json_response(
                {
                    "id": 5,
                    "name": "r",
                    "owner": {"login": "o"},
                    "private": True,
                    "default_branch": "develop",
                }
            )

        with make_client(handler) as client:
            repository = client.get_repository("o", "r")

        assert seen[0].headers["authorization"] == "Bearer test-token"
        assert repository.private is True
        assert repository.default_branch == "develop"
        assert repository.full_name == "o/r"

    def test_anonymous_client_sends_no_authorization(self) -> None:
        """Public repositories are readable without a token."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return json_response({"sha": "deadbeef"})

        with GitHubClient(token=None, transport=httpx.MockTransport(handler)) as client:
            assert client.get_branch_head_sha("o", "r", "main") == "deadbeef"

        assert "authorization" not in seen[0].headers
