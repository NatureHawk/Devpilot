"""Validates proposed edits against the indexed snapshot, and renders diffs.

An edit is an exact-match replacement: the model quotes the text it wants to
change, and that text must appear exactly once in the file as indexed. Anchoring
on content rather than line numbers is what makes a mismatch detectable — line
numbers would silently apply to the wrong place after any edit above them.

Nothing here writes to a repository. It produces a validated patch and a diff
for a human to look at.
"""

from __future__ import annotations

import difflib
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.source import SourceFile

logger = logging.getLogger(__name__)

# Unified-diff context lines. Three is the conventional default and enough to
# review a change without reprinting the file.
_DIFF_CONTEXT_LINES = 3


class PatchError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "patch_invalid"


class PatchMismatchError(PatchError):
    """The quoted text does not match the indexed source.

    Either the model misquoted, or the file moved on. Both mean the edit cannot
    be applied safely, and neither is resolved by guessing.
    """

    code = "patch_mismatch"


class StaleSnapshotError(AppError):
    """The repository was re-indexed after this proposal was generated."""

    status_code = status.HTTP_409_CONFLICT
    code = "patch_stale"


@dataclass(frozen=True, slots=True)
class ProposedEdit:
    """One replacement within one file."""

    path: str
    old_text: str
    new_text: str
    reason: str = ""


@dataclass(slots=True)
class FilePatch:
    """A validated change to one file, with the resulting content."""

    path: str
    original: str
    updated: str
    reasons: list[str] = field(default_factory=list)

    @property
    def added_lines(self) -> int:
        return sum(
            1
            for line in _diff_lines(self.original, self.updated, self.path)
            if line.startswith("+") and not line.startswith("+++")
        )

    @property
    def removed_lines(self) -> int:
        return sum(
            1
            for line in _diff_lines(self.original, self.updated, self.path)
            if line.startswith("-") and not line.startswith("---")
        )


@dataclass(slots=True)
class ValidatedPatch:
    files: list[FilePatch] = field(default_factory=list)

    @property
    def diff(self) -> str:
        return "\n".join(
            "\n".join(_diff_lines(patch.original, patch.updated, patch.path))
            for patch in self.files
        )


def parse_edits(raw: Any) -> list[ProposedEdit]:
    """Read edits out of the model's structured output.

    Strict: a malformed edit is rejected rather than repaired, because the whole
    safety argument rests on ``old_text`` being an exact quote.
    """
    if not isinstance(raw, list) or not raw:
        raise PatchError("The model did not produce any edits.")

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
        if not old_text:
            # Whole-file creation is out of scope: without an anchor there is
            # nothing to verify the edit against.
            raise PatchError(f"Edit {index + 1} has empty old_text, which cannot be anchored.")

        edits.append(
            ProposedEdit(
                path=path.strip(),
                old_text=old_text,
                new_text=new_text,
                reason=str(item.get("reason", "")).strip(),
            )
        )

    return edits


def validate_patch(
    session: Session,
    *,
    repository_id: uuid.UUID,
    edits: list[ProposedEdit],
    allowed_paths: set[str] | None = None,
) -> ValidatedPatch:
    """Apply edits to the indexed source in memory and confirm each one fits.

    ``allowed_paths`` restricts edits to files the model actually read during
    its investigation. A model cannot propose a change to a file it never
    opened, which rules out edits invented from a half-remembered path.
    """
    by_path: dict[str, list[ProposedEdit]] = {}
    for edit in edits:
        by_path.setdefault(edit.path, []).append(edit)

    patch = ValidatedPatch()

    for path, path_edits in by_path.items():
        if allowed_paths is not None and path not in allowed_paths:
            raise PatchError(
                f"The proposal edits '{path}', which was never read during investigation.",
                details={"path": path},
            )

        source_file = session.scalars(
            select(SourceFile).where(
                SourceFile.repository_id == repository_id, SourceFile.path == path
            )
        ).one_or_none()

        if source_file is None:
            raise PatchMismatchError(
                f"'{path}' is not part of the indexed snapshot.", details={"path": path}
            )

        original = source_file.content
        updated = original

        for edit in path_edits:
            occurrences = updated.count(edit.old_text)
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
            updated = updated.replace(edit.old_text, edit.new_text, 1)

        patch.files.append(
            FilePatch(
                path=path,
                original=original,
                updated=updated,
                reasons=[edit.reason for edit in path_edits if edit.reason],
            )
        )

    logger.info(
        "Patch validated repository_id=%s files=%d edits=%d",
        repository_id,
        len(patch.files),
        len(edits),
    )
    return patch


def assert_snapshot_current(*, proposal_sha: str | None, repository_sha: str | None) -> None:
    """Refuse to act on a patch built against a superseded snapshot.

    Called before approval rather than only at generation time: a repository can
    be re-indexed while a proposal sits in review, and the whole point of
    recording the sha is to catch exactly that.
    """
    if proposal_sha and repository_sha and proposal_sha != repository_sha:
        raise StaleSnapshotError(
            "The repository has been re-indexed since this change was proposed. "
            "Regenerate it against the current snapshot.",
            details={"proposed_against": proposal_sha, "current": repository_sha},
        )


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
