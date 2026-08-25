"""Typed views of the GitHub payloads we actually use.

Narrow on purpose: only the fields the application reads, so an unrelated change
to GitHub's response shape cannot break parsing.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class GitHubUser:
    id: int
    login: str
    name: str | None
    email: str | None
    avatar_url: str | None


@dataclass(frozen=True, slots=True)
class GitHubRepository:
    id: int
    owner: str
    name: str
    private: bool
    default_branch: str
    description: str | None = None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True, slots=True)
class TreeEntry:
    """One node in a git tree.

    ``size`` is absent for directories and, occasionally, for entries GitHub
    does not size; callers must tolerate None rather than assume 0.
    """

    path: str
    sha: str
    type: str  # "blob" | "tree" | "commit"
    size: int | None = None

    @property
    def is_blob(self) -> bool:
        return self.type == "blob"

    @property
    def is_tree(self) -> bool:
        return self.type == "tree"

    @property
    def is_submodule(self) -> bool:
        # Gitlinks point at another repository; there is nothing to fetch.
        return self.type == "commit"


@dataclass(frozen=True, slots=True)
class RepositoryTree:
    """A resolved file listing plus how completely it was resolved."""

    sha: str
    entries: list[TreeEntry] = field(default_factory=list)
    # True when GitHub truncated the recursive listing and the caller must
    # decide what to do; never ignored silently.
    truncated: bool = False

    @property
    def blobs(self) -> list[TreeEntry]:
        return [entry for entry in self.entries if entry.is_blob]
