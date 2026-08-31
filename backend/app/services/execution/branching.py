"""Deterministic, collision-resistant branch names for executed proposals.

Never derived from raw user text: the proposal's summary is the model's own
account of the change, but even that is only ever used as a lowercase,
character-filtered slug — never interpolated into a shell command or trusted
as a path, since this string ultimately becomes a git ref name over the
GitHub API.
"""

from __future__ import annotations

import re
import uuid

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
_MAX_SLUG_CHARS = 40


def generate_branch_name(proposal_id: uuid.UUID, summary: str) -> str:
    """``devpilot/change/{short-id}-{slug}``, or just the id if summary is unusable.

    The short id alone is already unique per proposal (it comes from the
    primary key), so the slug is cosmetic — worth truncating hard rather than
    worth failing over.
    """
    short_id = uuid.UUID(str(proposal_id)).hex[:8]
    slug = _SLUG_PATTERN.sub("-", summary.lower()).strip("-")[:_MAX_SLUG_CHARS].strip("-")
    suffix = f"-{slug}" if slug else ""
    return f"devpilot/change/{short_id}{suffix}"
