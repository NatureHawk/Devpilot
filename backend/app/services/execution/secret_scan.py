"""A lightweight, best-effort check for obvious secrets before a commit.

This is explicitly not a complete secret scanner — it catches the shapes of
credential that are cheap to recognise by pattern (vendor token prefixes,
PEM key headers) and nothing more. Its job is to stop the most common
accidents, not to be a security boundary on its own.

Only ever reports *what kind* of thing matched and *where* — never the
matched text itself, so the finding can be logged and shown to a user without
repeating the secret back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.patching import ProposedEdit

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"-----BEGIN[ A-Z]*PRIVATE KEY-----"), "a private key"),
    (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "a GitHub personal access token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "a GitHub fine-grained token"),
    (re.compile(r"\bgho_[A-Za-z0-9]{36}\b"), "a GitHub OAuth token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "a Google API key"),
    (re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"), "a Slack token"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "an API secret key"),
)


@dataclass(frozen=True, slots=True)
class SecretFinding:
    """Enough to explain a block, never enough to leak the secret."""

    path: str
    kind: str


def scan_edits(edits: list[ProposedEdit]) -> SecretFinding | None:
    """Check the content a commit would introduce. Stops at the first hit.

    Only ``new_text`` is checked: ``old_text`` is already in the indexed
    repository, so flagging it would block changes that have nothing to do
    with what this commit introduces.
    """
    for edit in edits:
        if _looks_like_env_file(edit.path):
            return SecretFinding(path=edit.path, kind="an environment file")

        for pattern, kind in _PATTERNS:
            if pattern.search(edit.new_text):
                return SecretFinding(path=edit.path, kind=kind)

    return None


def _looks_like_env_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name == ".env" or (name.startswith(".env.") and not name.startswith(".env.example"))
