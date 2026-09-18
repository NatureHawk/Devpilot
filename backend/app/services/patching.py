"""Validates proposed edits against the indexed snapshot, and renders diffs.

An edit is an exact-match replacement: the model quotes the text it wants to
change, and that text must appear exactly once in the file as indexed. Anchoring
on content rather than line numbers is what makes a mismatch detectable — line
numbers would silently apply to the wrong place after any edit above them.

The diff is derived, never supplied: it is rendered from the indexed file and
the validated replacements, then re-applied to the original to prove it
reproduces the patched file exactly. The model never authors diff text.

Nothing here writes to a repository. It produces a validated patch and a diff
for a human to look at.
"""

from __future__ import annotations

import difflib
import logging
import re
import uuid
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.change import ProposedChange
from app.models.repository import Repository
from app.models.source import SourceFile

logger = logging.getLogger(__name__)

# Unified-diff context lines. Three is the conventional default and enough to
# review a change without reprinting the file.
_DIFF_CONTEXT_LINES = 3

# Most edits one proposal may carry. A reviewable change is small; a proposal
# past this is a rewrite, not a patch.
MAX_EDITS = 20
_MAX_PATH_CHARS = 1000
_MAX_PATCHED_FILE_BYTES = 2_000_000
_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

STALE_MESSAGE = (
    "The repository has changed since this investigation, so the proposed change may "
    "no longer apply. Refresh the investigation against the current repository."
)


class PatchError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "patch_invalid"


class PatchMismatchError(PatchError):
    """The quoted text does not match the indexed source.

    Either the model misquoted, or the file moved on. Both mean the edit cannot
    be applied safely, and neither is resolved by guessing.
    """

    code = "patch_mismatch"


class PatchScopeError(PatchError):
    """The edit targets something outside what may be changed.

    A path outside the repository, a file that is not in the indexed snapshot,
    or a file the investigation never read.
    """

    code = "patch_out_of_scope"


class StaleSnapshotError(AppError):
    """The repository moved on after this proposal was generated."""

    status_code = status.HTTP_409_CONFLICT
    code = "patch_stale"


@dataclass(frozen=True, slots=True)
class ProposedEdit:
    """One replacement within one file."""

    path: str
    old_text: str
    new_text: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class AnchorLocation:
    """Where one edit's quoted text sits in the indexed file, 1-based inclusive."""

    path: str
    start_line: int
    end_line: int
    reason: str = ""


@dataclass(slots=True)
class FilePatch:
    """A validated change to one file, with the resulting content."""

    path: str
    original: str
    updated: str
    reasons: list[str] = field(default_factory=list)
    # The indexed blob the anchors were matched against. Execution compares it
    # with the live branch, so a file that changed on GitHub is never patched.
    blob_sha: str = ""
    anchors: list[AnchorLocation] = field(default_factory=list)

    @property
    def diff_lines(self) -> list[str]:
        return _diff_lines(self.original, self.updated, self.path)

    @property
    def additions(self) -> int:
        return sum(
            1 for line in self.diff_lines if line.startswith("+") and not line.startswith("+++")
        )

    @property
    def deletions(self) -> int:
        return sum(
            1 for line in self.diff_lines if line.startswith("-") and not line.startswith("---")
        )


@dataclass(slots=True)
class ValidatedPatch:
    files: list[FilePatch] = field(default_factory=list)

    @property
    def diff(self) -> str:
        return "\n".join("\n".join(patch.diff_lines) for patch in self.files)

    @property
    def blob_shas(self) -> dict[str, str]:
        return {patch.path: patch.blob_sha for patch in self.files}


def parse_edits(raw: Any) -> list[ProposedEdit]:
    """Read edits out of the model's structured output.

    Strict: a malformed edit is rejected rather than repaired, because the whole
    safety argument rests on ``old_text`` being an exact quote.
    """
    if not isinstance(raw, list) or not raw:
        raise PatchError("The model did not produce any edits.")
    if len(raw) > MAX_EDITS:
        raise PatchError(f"The proposal has {len(raw)} edits; at most {MAX_EDITS} are allowed.")

    edits: list[ProposedEdit] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise PatchError(f"Edit {index + 1} is not an object.")

        path = item.get("path")
        old_text = item.get("old_text")
        new_text = item.get("new_text")

        if not isinstance(path, str) or not path.strip():
            raise PatchError(f"Edit {index + 1} has no file path.")
        if not isinstance(old_text, str) or not isinstance(new_text, str):
            raise PatchError(f"Edit {index + 1} is missing old_text or new_text.")
        if old_text == new_text:
            raise PatchError(f"Edit {index + 1} does not change anything.")
        if not old_text.strip():
            # Whole-file creation is out of scope: without an anchor there is
            # nothing to verify the edit against.
            raise PatchError(f"Edit {index + 1} has empty old_text, which cannot be anchored.")

        edits.append(
            ProposedEdit(
                path=require_repository_path(path),
                old_text=old_text,
                new_text=new_text,
                reason=str(item.get("reason", "")).strip(),
            )
        )

    return edits


def require_repository_path(path: str) -> str:
    """Return ``path`` if it is a plain repository-relative path, else raise.

    Paths only ever match rows of the indexed snapshot, so traversal cannot
    reach the host either way — this rejects the attempt explicitly, so it is
    reported as out of scope rather than as a confusing "file not found".
    """
    cleaned = path.strip()
    segments = cleaned.split("/")
    if (
        not cleaned
        or len(cleaned) > _MAX_PATH_CHARS
        or cleaned.startswith("/")
        or "\\" in cleaned
        or "\x00" in cleaned
        or (len(cleaned) >= 2 and cleaned[1] == ":")
        or any(segment in ("", ".", "..") for segment in segments)
    ):
        raise PatchScopeError(
            "The path is outside the repository scope. Use a repository-relative path "
            "such as src/app.py.",
            details={"path": cleaned[:200]},
        )
    return cleaned


def validate_patch(
    session: Session,
    *,
    repository_id: uuid.UUID,
    edits: list[ProposedEdit],
    allowed_paths: set[str] | None = None,
    expected_blob_shas: dict[str, str] | None = None,
) -> ValidatedPatch:
    """Apply edits to the indexed source in memory and confirm each one fits.

    ``allowed_paths`` restricts edits to files the model actually read during
    its investigation. A model cannot propose a change to a file it never
    opened, which rules out edits invented from a half-remembered path.

    ``expected_blob_shas`` pins each file to the blob the proposal was built
    against. A re-index that changed the file makes the patch stale, even if the
    quoted text happens to still match somewhere.

    Every anchor is located in the *original* file, must occur exactly once, and
    must not overlap another edit's anchor. Matching against the original rather
    than progressively means an edit can never anchor on text a previous edit
    inserted, so the result does not depend on edit order.
    """
    by_path: dict[str, list[ProposedEdit]] = {}
    for edit in edits:
        by_path.setdefault(edit.path, []).append(edit)

    patch = ValidatedPatch()

    for path, path_edits in by_path.items():
        require_repository_path(path)
        if allowed_paths is not None and path not in allowed_paths:
            raise PatchScopeError(
                f"The proposal edits '{path}', which was never read during investigation.",
                details={"path": path},
            )

        source_file = session.scalars(
            select(SourceFile).where(
                SourceFile.repository_id == repository_id, SourceFile.path == path
            )
        ).one_or_none()

        if source_file is None:
            raise PatchScopeError(
                f"'{path}' is not part of the indexed snapshot, so it cannot be changed.",
                details={"path": path},
            )

        if expected_blob_shas is not None:
            expected = expected_blob_shas.get(path)
            if expected and expected != source_file.blob_sha:
                raise StaleSnapshotError(STALE_MESSAGE, details={"path": path})

        original = source_file.content
        updated, anchors = _apply_anchored_edits(path, original, path_edits)
        _assert_consistent(path, original, updated)

        patch.files.append(
            FilePatch(
                path=path,
                original=original,
                updated=updated,
                reasons=[edit.reason for edit in path_edits if edit.reason],
                blob_sha=source_file.blob_sha,
                anchors=anchors,
            )
        )

    logger.info(
        "Patch validated repository_id=%s files=%d edits=%d",
        repository_id,
        len(patch.files),
        len(edits),
    )
    return patch


def _apply_anchored_edits(
    path: str, original: str, edits: list[ProposedEdit]
) -> tuple[str, list[AnchorLocation]]:
    """Locate every anchor in ``original`` and splice in the replacements."""
    # Tools show files with "\n" line endings, so a quote from a CRLF file
    # arrives with "\n". Translate the quote to the file's own convention rather
    # than rewriting the file's line endings in the commit.
    crlf = "\r\n" in original

    spans: list[tuple[int, int, str, ProposedEdit]] = []
    for edit in edits:
        old_text, new_text = edit.old_text, edit.new_text
        if crlf:
            old_text = old_text.replace("\r\n", "\n").replace("\n", "\r\n")
            new_text = new_text.replace("\r\n", "\n").replace("\n", "\r\n")

        occurrences = original.count(old_text)
        if occurrences == 0:
            raise PatchMismatchError(
                f"The quoted text for '{path}' does not appear in the indexed file.",
                details={"path": path, "occurrences": 0},
            )
        if occurrences > 1:
            # Ambiguity is a failure, not a coin flip: applying to the first
            # match could silently change the wrong code.
            raise PatchMismatchError(
                f"The quoted text for '{path}' appears {occurrences} times, so the "
                "edit is ambiguous. A longer quote is needed.",
                details={"path": path, "occurrences": occurrences},
            )
        start = original.index(old_text)
        spans.append((start, start + len(old_text), new_text, edit))

    spans.sort(key=lambda span: span[0])
    for previous, current in pairwise(spans):
        if current[0] < previous[1]:
            raise PatchMismatchError(
                f"Two edits to '{path}' quote overlapping text. Combine them into one edit.",
                details={"path": path},
            )

    pieces: list[str] = []
    anchors: list[AnchorLocation] = []
    cursor = 0
    for start, end, new_text, edit in spans:
        pieces.append(original[cursor:start])
        pieces.append(new_text)
        cursor = end
        first_line = original.count("\n", 0, start) + 1
        last_line = first_line + original.count("\n", start, max(start, end - 1))
        anchors.append(
            AnchorLocation(path=path, start_line=first_line, end_line=last_line, reason=edit.reason)
        )
    pieces.append(original[cursor:])

    return "".join(pieces), anchors


def _assert_consistent(path: str, original: str, updated: str) -> None:
    """Prove the diff a reviewer sees is exactly the change that will be committed.

    Renders the unified diff, re-applies its hunks to the original, and requires
    the result to equal the updated content line for line. A diff that does not
    reproduce the file — or a change with no visible diff at all — is refused
    rather than shown.
    """
    if updated == original:
        raise PatchError(f"The edits to '{path}' do not change the file.", details={"path": path})
    if len(updated.encode("utf-8")) > _MAX_PATCHED_FILE_BYTES:
        raise PatchError(f"The patched '{path}' would be too large.", details={"path": path})

    diff = _diff_lines(original, updated, path)
    if not diff:
        raise PatchError(
            f"The edits to '{path}' change only line endings or a final newline, which "
            "cannot be reviewed as a diff.",
            details={"path": path},
        )

    if apply_unified_diff(original.splitlines(), diff) != updated.splitlines():
        logger.error("Generated diff does not reproduce the patched file path=%s", path)
        raise PatchError(
            f"The diff for '{path}' could not be verified against the patched file.",
            details={"path": path},
        )


def apply_unified_diff(original_lines: list[str], diff: list[str]) -> list[str]:
    """Apply one file's unified diff to its original lines.

    Deliberately strict and independent of :mod:`difflib`: every context and
    removed line must match the original exactly, or this raises. It is the
    check that the diff shown for review is self-consistent.
    """
    result: list[str] = []
    position = 0  # index into original_lines
    index = 0
    while index < len(diff):
        line = diff[index]
        if line.startswith(("--- ", "+++ ")):
            index += 1
            continue
        match = _HUNK_HEADER.match(line)
        if match is None:
            raise PatchError("Malformed diff hunk header.")
        old_start = int(match.group(1))
        old_count = int(match.group(2)) if match.group(2) is not None else 1
        # A zero-length old range names the line *after which* lines are added.
        hunk_start = old_start if old_count == 0 else old_start - 1
        if hunk_start < position:
            raise PatchError("Diff hunks overlap.")
        result.extend(original_lines[position:hunk_start])
        position = hunk_start
        index += 1
        while index < len(diff) and not diff[index].startswith("@@"):
            body = diff[index]
            marker, text = body[:1], body[1:]
            if marker in (" ", "-"):
                if position >= len(original_lines) or original_lines[position] != text:
                    raise PatchError("Diff context does not match the original file.")
                if marker == " ":
                    result.append(text)
                position += 1
            elif marker == "+":
                result.append(text)
            elif marker != "\\":  # "\ No newline at end of file" carries no line
                raise PatchError("Malformed diff line.")
            index += 1
    result.extend(original_lines[position:])
    return result


def assert_snapshot_current(*, proposal_sha: str | None, repository_sha: str | None) -> None:
    """Refuse to act on a patch built against a superseded snapshot.

    Called before approval rather than only at generation time: a repository can
    be re-indexed while a proposal sits in review, and the whole point of
    recording the sha is to catch exactly that.
    """
    if proposal_sha and repository_sha and proposal_sha != repository_sha:
        raise StaleSnapshotError(
            STALE_MESSAGE,
            details={"proposed_against": proposal_sha, "current": repository_sha},
        )


def revalidate_against_snapshot(
    session: Session, *, proposal: ProposedChange, repository: Repository
) -> ValidatedPatch:
    """Re-check a stored proposal against the repository as currently indexed.

    The snapshot sha must be unchanged, every file must still be the blob the
    proposal was built against, and every anchor must still match exactly once.
    Any failure is staleness: the patch no longer describes the current code.
    """
    assert_snapshot_current(
        proposal_sha=proposal.indexed_commit_sha, repository_sha=repository.indexed_commit_sha
    )
    try:
        edits = parse_edits(proposal.edits)
        return validate_patch(
            session,
            repository_id=repository.id,
            edits=edits,
            allowed_paths={edit.path for edit in edits},
            expected_blob_shas=stored_blob_shas(proposal),
        )
    except PatchError as exc:
        raise StaleSnapshotError(STALE_MESSAGE, details=exc.details) from exc


def stored_blob_shas(proposal: ProposedChange) -> dict[str, str]:
    """The blob each changed file was validated against, from the stored report."""
    files = (proposal.report or {}).get("files") or []
    return {
        str(item["path"]): str(item["blob_sha"])
        for item in files
        if isinstance(item, dict) and item.get("path") and item.get("blob_sha")
    }


def _diff_lines(original: str, updated: str, path: str) -> list[str]:
    """Render one file's unified diff.

    ``a/`` and ``b/`` prefixes match git's convention, so the output is readable
    by anything that already understands a diff.
    """
    return list(
        difflib.unified_diff(
            original.splitlines(),
            updated.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
            n=_DIFF_CONTEXT_LINES,
        )
    )
