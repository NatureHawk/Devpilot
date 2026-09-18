"""Synchronous GitHub REST client.

Synchronous on purpose: routes are declared ``def`` and run in FastAPI's
threadpool alongside synchronous SQLAlchemy, so an async client here would mean
mixing paradigms for no gain. Concurrency where it matters — downloading many
blobs — comes from a bounded thread pool in :meth:`get_blobs`.

The client owns retries, error translation and rate-limit detection so callers
deal in :mod:`app.integrations.github.errors`, never in status codes.
"""

from __future__ import annotations

import base64
import binascii
import logging
import time
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import islice
from types import TracebackType
from typing import Any
from urllib.parse import quote

import httpx

from app.integrations.github.errors import (
    GitHubConflictError,
    GitHubError,
    GitHubForbiddenError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubUnauthorizedError,
    GitHubUnavailableError,
)
from app.integrations.github.models import (
    GitHubPullRequest,
    GitHubRepository,
    GitHubUser,
    RepositoryTree,
    TreeEntry,
)

logger = logging.getLogger(__name__)

API_VERSION = "2022-11-28"
USER_AGENT = "DevPilot"

# Transient server-side failures are worth one or two more attempts; anything
# else is a definite answer and retrying only wastes rate limit.
_RETRY_STATUSES = frozenset({500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 0.5

# Ceiling on subtree requests when falling back from a truncated recursive tree.
# A repository needing more than this is beyond what synchronous indexing should
# attempt, and the caller is told so rather than given a partial listing.
MAX_SUBTREE_REQUESTS = 300


class GitHubClient:
    """Thin wrapper over the GitHub REST API.

    Instances hold an ``httpx.Client`` and must be closed; use as a context
    manager. ``token`` may be None, which is valid for public repositories at
    GitHub's unauthenticated rate limit.
    """

    def __init__(
        self,
        *,
        token: str | None,
        base_url: str = "https://api.github.com",
        timeout_seconds: float = 20.0,
        max_concurrency: int = 8,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._token = token
        self._max_concurrency = max_concurrency
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        # `transport` is a test seam: it lets the suite drive real client code —
        # retries, error translation, tree walking — without a network or a
        # local server. Production callers leave it unset.
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout_seconds,
            follow_redirects=True,
            transport=transport,
        )

    def __enter__(self) -> GitHubClient:
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

    # ---- request plumbing -------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.TimeoutException as exc:
                last_error = exc
                logger.warning("GitHub request timed out (attempt %d): %s", attempt, path)
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning("GitHub request failed (attempt %d): %s", attempt, path)
            else:
                if response.status_code in _RETRY_STATUSES and attempt < _MAX_ATTEMPTS:
                    time.sleep(_BACKOFF_BASE_SECONDS * attempt)
                    continue
                if response.is_success:
                    return response
                self._raise_for_status(response, path)

            if attempt < _MAX_ATTEMPTS:
                time.sleep(_BACKOFF_BASE_SECONDS * attempt)

        raise GitHubUnavailableError("GitHub could not be reached.") from last_error

    @staticmethod
    def _raise_for_status(response: httpx.Response, path: str) -> None:
        """Translate an unsuccessful response into a typed error.

        The response body is never propagated: it can echo request context, and
        the frontend only needs the category.
        """
        status = response.status_code
        remaining = response.headers.get("x-ratelimit-remaining")

        # A 403 with an exhausted quota is a rate limit, not a permission
        # problem; 429 is the secondary limit. They are different causes with
        # the same remedy, so both surface as rate limiting.
        if status == 429 or (status == 403 and remaining == "0"):
            raise GitHubRateLimitError("GitHub's rate limit has been reached. Try again shortly.")
        if status == 401:
            raise GitHubUnauthorizedError(
                "GitHub rejected the credentials. Reconnect your GitHub account."
            )
        if status == 403:
            raise GitHubForbiddenError("GitHub denied access to this resource.")
        if status == 404:
            raise GitHubNotFoundError("The requested GitHub resource does not exist.")
        if status == 422:
            # The Git Data API uses 422 for "ref already exists" and similar
            # state conflicts, not for payload validation as elsewhere.
            raise GitHubConflictError("GitHub rejected the request: the resource already exists.")

        logger.warning("Unexpected GitHub status %s for %s", status, path)
        raise GitHubUnavailableError(f"GitHub returned an unexpected status ({status}).")

    def _get_json(self, path: str, **kwargs: Any) -> Any:
        return self._request("GET", path, **kwargs).json()

    # ---- identity ---------------------------------------------------------

    def get_authenticated_user(self) -> GitHubUser:
        payload = self._get_json("/user")
        return GitHubUser(
            id=int(payload["id"]),
            login=str(payload["login"]),
            name=payload.get("name"),
            email=payload.get("email"),
            avatar_url=payload.get("avatar_url"),
        )

    # ---- repository -------------------------------------------------------

    def get_repository(self, owner: str, name: str) -> GitHubRepository:
        return _repository_from_payload(self._get_json(f"/repos/{owner}/{name}"))

    def get_branch_head_sha(self, owner: str, name: str, branch: str) -> str:
        """Resolve a branch to the commit it currently points at.

        Indexing pins to this sha so the snapshot is identifiable, and so a
        later run can tell whether anything changed.
        """
        payload = self._get_json(f"/repos/{owner}/{name}/commits/{branch}")
        return str(payload["sha"])

    # ---- trees ------------------------------------------------------------

    def get_tree(
        self, owner: str, name: str, sha: str, *, recursive: bool = True
    ) -> RepositoryTree:
        """Fetch one tree object.

        ``truncated`` is surfaced, never smoothed over: an incomplete listing
        that looked complete would silently drop files from the index.
        """
        params = {"recursive": "1"} if recursive else None
        payload = self._get_json(f"/repos/{owner}/{name}/git/trees/{sha}", params=params)

        entries = [
            TreeEntry(
                path=str(item["path"]),
                sha=str(item["sha"]),
                type=str(item["type"]),
                size=int(item["size"]) if item.get("size") is not None else None,
            )
            for item in payload.get("tree", [])
        ]
        return RepositoryTree(
            sha=str(payload.get("sha", sha)),
            entries=entries,
            truncated=bool(payload.get("truncated", False)),
        )

    def get_full_tree(self, owner: str, name: str, sha: str) -> RepositoryTree:
        """Resolve a complete file listing, walking subtrees if needed.

        The recursive endpoint is one request but may truncate. When it does,
        each directory is fetched individually and the paths are rebuilt, which
        costs more requests but yields a listing that is actually complete.

        Raises :class:`GitHubError` if even the fallback cannot finish inside
        :data:`MAX_SUBTREE_REQUESTS`, so the caller fails loudly rather than
        indexing part of a repository.
        """
        tree = self.get_tree(owner, name, sha, recursive=True)
        if not tree.truncated:
            return tree

        logger.info(
            "Recursive tree truncated for %s/%s; walking subtrees individually", owner, name
        )
        entries: list[TreeEntry] = []
        # (sha, path prefix) of directories still to expand.
        pending: list[tuple[str, str]] = [(sha, "")]
        requests_made = 0

        while pending:
            subtree_sha, prefix = pending.pop()
            if requests_made >= MAX_SUBTREE_REQUESTS:
                raise GitHubError(
                    "This repository's file listing is too large for DevPilot to "
                    "retrieve completely.",
                    details={"reason": "tree_truncated", "subtree_requests": requests_made},
                )

            subtree = self.get_tree(owner, name, subtree_sha, recursive=False)
            requests_made += 1

            for entry in subtree.entries:
                full_path = f"{prefix}{entry.path}"
                if entry.is_tree:
                    pending.append((entry.sha, f"{full_path}/"))
                    continue
                if entry.is_submodule:
                    continue
                entries.append(
                    TreeEntry(path=full_path, sha=entry.sha, type=entry.type, size=entry.size)
                )

        logger.info(
            "Resolved truncated tree for %s/%s with %d subtree requests", owner, name, requests_made
        )
        return RepositoryTree(sha=sha, entries=entries, truncated=False)

    # ---- blobs ------------------------------------------------------------

    def get_blob(self, owner: str, name: str, sha: str) -> bytes:
        """Fetch one blob's raw bytes.

        The JSON blob endpoint is used rather than the raw media type because it
        works uniformly for every file and reports the encoding explicitly.
        """
        payload = self._get_json(f"/repos/{owner}/{name}/git/blobs/{sha}")
        encoding = payload.get("encoding")
        content = payload.get("content", "")

        if encoding == "base64":
            try:
                return base64.b64decode(content)
            except (binascii.Error, ValueError) as exc:
                raise GitHubError(f"GitHub returned an undecodable blob ({sha}).") from exc
        if encoding == "utf-8":
            return str(content).encode("utf-8")

        raise GitHubError(f"GitHub returned a blob in an unsupported encoding ({encoding}).")

    def get_blobs(
        self, owner: str, name: str, shas: Iterable[str]
    ) -> Iterator[tuple[str, bytes | Exception]]:
        """Fetch many blobs with bounded concurrency and bounded memory.

        Yields ``(sha, bytes)`` or ``(sha, exception)``, so one failed file does
        not abort the batch and the caller decides what a failure means.

        Work is submitted in windows rather than all at once: submitting every
        blob up front would keep each downloaded file alive until the consumer
        reached it, which for a large repository means holding the whole
        repository in memory. A window caps that at a few files at a time.
        """
        sha_iterator = iter(shas)
        window_size = self._max_concurrency * 2

        with ThreadPoolExecutor(max_workers=self._max_concurrency) as pool:
            while True:
                window = list(islice(sha_iterator, window_size))
                if not window:
                    return

                futures = {pool.submit(self.get_blob, owner, name, sha): sha for sha in window}
                for future in as_completed(futures):
                    sha = futures[future]
                    try:
                        yield sha, future.result()
                    except Exception as exc:
                        yield sha, exc

    # ---- write path: branch, commit, pull request --------------------------
    #
    # Everything below goes through GitHub's Git Data API (blobs/trees/commits/
    # refs) rather than a local clone and shell `git`. That means: no subprocess,
    # no shell string ever built from repository or model content, no working
    # directory to isolate or clean up, and no credential ever touches a
    # filesystem or a `git remote` URL. A commit is built purely from data this
    # process already validated, and pushing a branch is a single REST call.

    def get_commit_tree_sha(self, owner: str, name: str, commit_sha: str) -> str:
        """The tree a commit points at — the base a new tree is built from."""
        payload = self._get_json(f"/repos/{owner}/{name}/git/commits/{commit_sha}")
        return str(payload["tree"]["sha"])

    def create_blob(self, owner: str, name: str, content: str) -> str:
        """Store one file's content as a blob, returning its sha.

        Sent as UTF-8 text rather than base64: every file DevPilot indexes and
        patches is already decoded text, and this avoids a redundant encode.
        """
        payload = self._request(
            "POST",
            f"/repos/{owner}/{name}/git/blobs",
            json={"content": content, "encoding": "utf-8"},
        ).json()
        return str(payload["sha"])

    def create_tree(
        self, owner: str, name: str, *, base_tree_sha: str, entries: list[dict[str, str]]
    ) -> str:
        """Build a new tree from a base plus changed-file entries only.

        Unlisted paths are inherited from ``base_tree_sha`` unchanged — this is
        what guarantees only the approved files are touched, without needing to
        enumerate or diff the rest of the repository.
        """
        payload = self._request(
            "POST",
            f"/repos/{owner}/{name}/git/trees",
            json={"base_tree": base_tree_sha, "tree": entries},
        ).json()
        return str(payload["sha"])

    def create_commit(
        self, owner: str, name: str, *, message: str, tree_sha: str, parent_sha: str
    ) -> str:
        payload = self._request(
            "POST",
            f"/repos/{owner}/{name}/git/commits",
            json={"message": message, "tree": tree_sha, "parents": [parent_sha]},
        ).json()
        return str(payload["sha"])

    def create_branch(self, owner: str, name: str, *, branch: str, commit_sha: str) -> None:
        """Point a new ref at an existing commit — the equivalent of a push.

        A 422 here means the ref already exists, translated by
        :meth:`_raise_for_status` into :class:`GitHubConflictError`.
        """
        self._request(
            "POST",
            f"/repos/{owner}/{name}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": commit_sha},
        )

    def create_pull_request(
        self, owner: str, name: str, *, title: str, body: str, head: str, base: str
    ) -> GitHubPullRequest:
        payload = self._request(
            "POST",
            f"/repos/{owner}/{name}/pulls",
            json={"title": title, "body": body, "head": head, "base": base},
        ).json()
        return _pull_request_from_payload(payload)

    # ---- write-path recovery reads ----------------------------------------
    #
    # Used to make execution idempotent across a crash or a retried request: a
    # branch or pull request that a previous attempt already created is found
    # and adopted, never duplicated.

    def get_branch_ref_sha(self, owner: str, name: str, branch: str) -> str | None:
        """The commit a branch ref points at, or None if the branch does not exist."""
        try:
            payload = self._get_json(
                f"/repos/{owner}/{name}/git/ref/heads/{quote(branch, safe='/')}"
            )
        except GitHubNotFoundError:
            return None
        # A prefix match returns a list of refs rather than the one asked for.
        if not isinstance(payload, dict) or payload.get("ref") != f"refs/heads/{branch}":
            return None
        return str(payload["object"]["sha"])

    def get_git_commit(self, owner: str, name: str, commit_sha: str) -> tuple[str, list[str]]:
        """A commit's tree sha and parent shas."""
        payload = self._get_json(f"/repos/{owner}/{name}/git/commits/{commit_sha}")
        parents = [str(parent["sha"]) for parent in payload.get("parents") or []]
        return str(payload["tree"]["sha"]), parents

    def get_file_blob_sha(self, owner: str, name: str, path: str, *, ref: str) -> str | None:
        """The blob sha of one file at ``ref``, or None if the file is absent there."""
        try:
            payload = self._get_json(
                f"/repos/{owner}/{name}/contents/{quote(path, safe='/')}", params={"ref": ref}
            )
        except GitHubNotFoundError:
            return None
        if not isinstance(payload, dict) or payload.get("type") != "file":
            return None
        return str(payload["sha"])

    def find_open_pull_request(
        self, owner: str, name: str, *, head: str, base: str
    ) -> GitHubPullRequest | None:
        """The open pull request from ``head`` into ``base``, if one exists."""
        payload = self._get_json(
            f"/repos/{owner}/{name}/pulls",
            params={"head": f"{owner}:{head}", "base": base, "state": "open", "per_page": 5},
        )
        for item in payload if isinstance(payload, list) else []:
            if (item.get("head") or {}).get("ref") == head:
                return _pull_request_from_payload(item)
        return None


def _pull_request_from_payload(payload: dict[str, Any]) -> GitHubPullRequest:
    return GitHubPullRequest(
        id=int(payload["id"]),
        number=int(payload["number"]),
        html_url=str(payload["html_url"]),
        state=str(payload["state"]),
        head_ref=str(payload["head"]["ref"]),
        base_ref=str(payload["base"]["ref"]),
    )


def _repository_from_payload(payload: dict[str, Any]) -> GitHubRepository:
    owner = payload.get("owner") or {}
    return GitHubRepository(
        id=int(payload["id"]),
        owner=str(owner.get("login", "")),
        name=str(payload["name"]),
        private=bool(payload.get("private", False)),
        default_branch=str(payload.get("default_branch") or "main"),
        description=payload.get("description"),
    )
