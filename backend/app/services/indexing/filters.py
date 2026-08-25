"""File filtering policy.

Every decision about whether a repository file is indexed lives here. The
indexing service asks this module and records the answer; it never inspects a
path or an extension itself, so the policy stays in one testable place.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.integrations.github.models import TreeEntry
from app.services.indexing.languages import Language, detect_language

# Directories whose contents are installed, generated, or vendored. Matched as
# whole path segments, so a file legitimately named "build.py" is unaffected.
EXCLUDED_DIRECTORIES: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "bower_components",
        "vendor",
        ".venv",
        "venv",
        "env",
        "site-packages",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        "out",
        "target",
        "obj",
        ".next",
        ".nuxt",
        ".turbo",
        ".gradle",
        ".terraform",
        "coverage",
        "htmlcov",
        ".idea",
        ".vscode",
    }
)

# Generated lockfiles: large, machine-written, and of no use to code search.
EXCLUDED_FILENAMES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "cargo.lock",
        "composer.lock",
        "gemfile.lock",
        "go.sum",
    }
)

# Build products that keep a source extension.
EXCLUDED_SUFFIXES: tuple[str, ...] = (
    ".min.js",
    ".min.css",
    ".bundle.js",
    ".map",
    ".lock",
)

# A NUL byte inside this many leading bytes means binary. Git uses the same
# heuristic on a similar window.
BINARY_SNIFF_BYTES = 8_000


class SkipReason(enum.StrEnum):
    """Why a file was not indexed. Reported in aggregate, never as a failure."""

    EXCLUDED_DIRECTORY = "excluded_directory"
    EXCLUDED_FILE = "excluded_file"
    UNSUPPORTED_TYPE = "unsupported_type"
    TOO_LARGE = "too_large"
    BINARY = "binary"
    SUBMODULE = "submodule"
    EMPTY = "empty"
    FILE_LIMIT_REACHED = "file_limit_reached"
    BYTE_BUDGET_REACHED = "byte_budget_reached"
    DECODE_FAILED = "decode_failed"


@dataclass(frozen=True, slots=True)
class FilterLimits:
    """Resource ceilings for one indexing run."""

    max_file_bytes: int
    max_total_bytes: int
    max_files: int


@dataclass(frozen=True, slots=True)
class FileDecision:
    """The verdict for one tree entry."""

    include: bool
    reason: SkipReason | None = None
    language: Language | None = None


def is_excluded_path(path: str) -> SkipReason | None:
    """Path-only exclusions, checked before anything is downloaded."""
    parts = PurePosixPath(path).parts
    # Every segment except the filename is a directory.
    for segment in parts[:-1]:
        if segment.lower() in EXCLUDED_DIRECTORIES:
            return SkipReason.EXCLUDED_DIRECTORY

    name = parts[-1].lower() if parts else ""
    if name in EXCLUDED_FILENAMES:
        return SkipReason.EXCLUDED_FILE
    if any(name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES):
        return SkipReason.EXCLUDED_FILE

    return None


def looks_binary(content: bytes) -> bool:
    """Detect binary content by NUL byte in the leading window."""
    return b"\x00" in content[:BINARY_SNIFF_BYTES]


def decode_text(content: bytes) -> str | None:
    """Decode source as UTF-8, or return None if it is not text.

    Strict decoding on purpose: replacing undecodable bytes would silently
    corrupt the stored source and every chunk cut from it.
    """
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


class FileFilter:
    """Applies the filtering policy to tree entries.

    Stateless: running totals are passed in, so the same filter instance can be
    reused and each decision is reproducible from its arguments alone.
    """

    def __init__(self, limits: FilterLimits) -> None:
        self._limits = limits

    @property
    def limits(self) -> FilterLimits:
        return self._limits

    def evaluate(
        self, entry: TreeEntry, *, accepted_files: int = 0, accepted_bytes: int = 0
    ) -> FileDecision:
        """Decide an entry using only its metadata — no download required.

        Cheap checks come first so an excluded directory never costs a size
        lookup, and budget checks come last so a file is only rejected for
        budget once it would otherwise have been accepted.
        """
        if entry.is_submodule:
            return FileDecision(False, SkipReason.SUBMODULE)
        if not entry.is_blob:
            return FileDecision(False, SkipReason.UNSUPPORTED_TYPE)

        excluded = is_excluded_path(entry.path)
        if excluded is not None:
            return FileDecision(False, excluded)

        language = detect_language(entry.path)
        if language is None:
            return FileDecision(False, SkipReason.UNSUPPORTED_TYPE)

        size = entry.size or 0
        if size == 0:
            return FileDecision(False, SkipReason.EMPTY)
        if size > self._limits.max_file_bytes:
            return FileDecision(False, SkipReason.TOO_LARGE, language)

        if accepted_files >= self._limits.max_files:
            return FileDecision(False, SkipReason.FILE_LIMIT_REACHED, language)
        if accepted_bytes + size > self._limits.max_total_bytes:
            return FileDecision(False, SkipReason.BYTE_BUDGET_REACHED, language)

        return FileDecision(True, None, language)
