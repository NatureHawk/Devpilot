"""Runs the tools the model asks for.

The backend owns execution entirely: it validates every argument, scopes every
lookup to the one repository under investigation, and bounds every result. The
model chooses *which* tool and *what* to look for; it never chooses what the
tool is allowed to reach.

Every piece of source a tool returns is registered as citable evidence with a
stable label (``S1``, ``S2`` …) and its exact location, so the investigation's
claims can be traced back to the lines they rest on.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.repository import Repository
from app.models.source import CodeChunk, SourceFile
from app.services import retrieval as retrieval_service
from app.services.execution.secret_scan import looks_like_env_file, redact_secrets
from app.services.llm import ToolCall, ToolResult
from app.services.patching import PatchScopeError, require_repository_path
from app.services.tools.registry import (
    FIND_SYMBOL,
    MAX_READ_LINES,
    MAX_SEARCH_RESULTS,
    MAX_SYMBOL_RESULTS,
    READ_FILE,
    SEARCH_CODE,
    TOOL_NAMES,
)

logger = logging.getLogger(__name__)

# Search and symbol hits are pointers, not the evidence itself: each excerpt is
# capped so a handful of results cannot crowd out a proper read_file.
_MAX_EXCERPT_CHARS = 700


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """One citable piece of repository evidence, as the model was shown it."""

    id: str
    path: str
    start_line: int
    end_line: int
    symbol: str | None
    # How it was found: "retrieval" for the initial search, else the tool name.
    origin: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "symbol": self.symbol,
            "origin": self.origin,
        }


@dataclass(slots=True)
class ToolActivity:
    """A bounded record of one tool call, safe to persist and to show a user.

    Carries no file contents — only what happened, so the UI can say
    "Searching codebase…" and a reviewer can see the shape of the investigation
    without exposing reasoning or source.
    """

    tool: str
    duration_ms: int
    ok: bool
    detail: str = ""


@dataclass(slots=True)
class ToolContext:
    """State carried across one investigation.

    ``files_read`` is the security-relevant part: a model may only propose edits
    to files it actually read, and this is the record of that.
    """

    session: Session
    repository: Repository
    settings: Settings
    files_read: dict[str, str] = field(default_factory=dict)
    output_chars_used: int = 0
    activity: list[ToolActivity] = field(default_factory=list)
    evidence: dict[str, EvidenceRecord] = field(default_factory=dict)
    # Line ranges of each file actually shown to the model, so a later read can
    # report what is still unseen.
    read_ranges: dict[str, list[tuple[int, int]]] = field(default_factory=dict)

    def record_read(self, path: str, start: int, end: int) -> None:
        self.read_ranges.setdefault(path, []).append((start, end))

    def unread_ranges(self, path: str, total: int) -> list[tuple[int, int]]:
        """Line ranges of ``path`` the model has not been shown yet.

        Models paginate a file by hand and miscount, leaving a gap they then
        conclude the code is absent from. Reporting the gap back is what stops
        "I could not find it" from meaning "I never looked there".
        """
        seen = sorted(self.read_ranges.get(path, []))
        gaps: list[tuple[int, int]] = []
        cursor = 1
        for start, end in seen:
            if start > cursor:
                gaps.append((cursor, start - 1))
            cursor = max(cursor, end + 1)
        if cursor <= total:
            gaps.append((cursor, total))
        return gaps

    def cite(
        self,
        *,
        path: str,
        start_line: int,
        end_line: int,
        symbol: str | None,
        origin: str,
    ) -> str:
        """Register evidence and return its label. The same span keeps one label."""
        for record in self.evidence.values():
            if (record.path, record.start_line, record.end_line) == (path, start_line, end_line):
                return record.id
        label = f"S{len(self.evidence) + 1}"
        self.evidence[label] = EvidenceRecord(
            id=label,
            path=path,
            start_line=start_line,
            end_line=end_line,
            symbol=symbol,
            origin=origin,
        )
        return label


class ToolBudgetExceeded(Exception):
    """The investigation has pulled in as much source as it is allowed."""


def execute(context: ToolContext, call: ToolCall) -> ToolResult:
    """Run one tool call and return a result for the model.

    Never raises for a bad request from the model: an unknown tool, a malformed
    argument or a missing file all come back as ``is_error`` results, because
    the model recovers from being told, and an exception would abort an
    investigation over a recoverable mistake.
    """
    started = time.monotonic()
    ok = True
    detail = ""

    try:
        if call.name not in TOOL_NAMES:
            # Checked before the budget: an invented tool is an error in its own
            # right, whatever budget remains.
            ok = False
            payload: dict[str, Any] = {
                "error": f"Unknown tool '{call.name[:80]}'. Available tools: "
                + ", ".join(sorted(TOOL_NAMES))
                + "."
            }
            detail = "unknown tool"
        elif context.output_chars_used >= context.settings.effective_agent_tool_output_chars:
            raise ToolBudgetExceeded(
                "Tool output budget exhausted. Conclude from the evidence already gathered."
            )
        elif call.name == SEARCH_CODE:
            payload, detail = _search_code(context, call.arguments)
        elif call.name == READ_FILE:
            payload, detail = _read_file(context, call.arguments)
        else:
            payload, detail = _find_symbol(context, call.arguments)

        if "error" in payload:
            ok = False

        content = json.dumps(payload, ensure_ascii=False)
        context.output_chars_used += len(content)

    except ToolBudgetExceeded as exc:
        ok = False
        content = json.dumps({"error": str(exc)})
        detail = "budget exhausted"
    except PatchScopeError as exc:
        ok = False
        content = json.dumps({"error": exc.message})
        detail = "path out of scope"
    except (KeyError, TypeError, ValueError) as exc:
        # Malformed arguments from the model.
        ok = False
        content = json.dumps({"error": f"Invalid arguments: {exc}"})
        detail = "invalid arguments"
    except Exception:
        # A genuine backend fault. Logged with a traceback; the model is told
        # only that the tool failed, so nothing internal leaks into context.
        logger.exception("Tool %s failed", call.name)
        ok = False
        content = json.dumps({"error": "The tool failed to run."})

    duration_ms = int((time.monotonic() - started) * 1000)
    tool_name = call.name if call.name in TOOL_NAMES else call.name[:40]
    context.activity.append(
        ToolActivity(tool=tool_name, duration_ms=duration_ms, ok=ok, detail=detail[:200])
    )
    logger.info("Tool %s ok=%s duration_ms=%d", tool_name, ok, duration_ms)

    return ToolResult(tool_use_id=call.id, content=content, is_error=not ok)


# ---- tools ----------------------------------------------------------------


def _search_code(context: ToolContext, arguments: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Hybrid search, reusing the one retrieval implementation."""
    query = _require_string(arguments, "query")[:500]
    limit = _bounded_int(arguments.get("limit", 5), 1, MAX_SEARCH_RESULTS)

    result = retrieval_service.retrieve(
        context.session,
        repository=context.repository,
        query=query,
        top_k=limit,
        settings=context.settings,
    )

    results = []
    for source in result.sources[:limit]:
        excerpt, truncated = _excerpt(source.content)
        results.append(
            {
                "id": context.cite(
                    path=source.file_path,
                    start_line=source.start_line,
                    end_line=source.end_line,
                    symbol=source.qualified_symbol,
                    origin=SEARCH_CODE,
                ),
                "path": source.file_path,
                "symbol": source.qualified_symbol,
                "kind": source.chunk_type,
                "start_line": source.start_line,
                "end_line": source.end_line,
                "content": excerpt,
                "content_truncated": truncated,
            }
        )

    return (
        {
            "results": results,
            "note": "Excerpts are abbreviated. Use read_file with a line range before "
            "quoting code in an edit.",
        },
        f'"{query[:60]}" → {len(results)} results',
    )


def _read_file(context: ToolContext, arguments: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Read a bounded line range from one indexed file.

    Lookup is by exact path within this repository's rows. There is no
    filesystem access, so a path like ``../../etc/passwd`` could never match a
    row — and it is rejected explicitly before the lookup, so the model is told
    it asked for something out of scope.
    """
    path = require_repository_path(_require_string(arguments, "path"))

    if looks_like_env_file(path):
        return ({"error": "Environment files are not readable."}, f"{path} (refused)")

    stmt = select(SourceFile).where(
        SourceFile.repository_id == context.repository.id, SourceFile.path == path
    )
    source_file = context.session.scalars(stmt).one_or_none()

    if source_file is None:
        return (
            {
                "error": (
                    f"No indexed file at '{path}'. Paths are repository-relative; "
                    "use search_code or find_symbol to find the correct path."
                )
            },
            f"{path} (not found)",
        )

    lines = source_file.content.splitlines()
    total = len(lines)

    start = _bounded_int(arguments.get("start_line", 1), 1, max(total, 1))
    requested_end = arguments.get("end_line")
    end = _bounded_int(requested_end, start, total) if requested_end is not None else total
    # Always cap the window, however wide a range was asked for.
    end = min(end, start + MAX_READ_LINES - 1, total)

    # And cap by size, on a whole-line boundary: an anchor quoted from a
    # half-line would never match.
    max_chars = context.settings.effective_agent_tool_result_chars
    used = 0
    last = start - 1
    for number in range(start, end + 1):
        cost = len(lines[number - 1]) + 1
        if used + cost > max_chars and number > start:
            break
        used += cost
        last = number
    end = max(last, start) if total else 0

    excerpt, redactions = redact_secrets("\n".join(lines[start - 1 : end]))

    # Remember the whole file, not the excerpt: patch validation matches
    # old_text against full content, and the model may read several ranges.
    context.files_read[source_file.path] = source_file.content

    context.record_read(source_file.path, start, end)

    payload: dict[str, Any] = {
        "id": context.cite(
            path=source_file.path,
            start_line=start,
            end_line=end,
            symbol=None,
            origin=READ_FILE,
        ),
        "path": source_file.path,
        "start_line": start,
        "end_line": end,
        "total_lines": total,
        "truncated": end < total,
        "content": excerpt,
    }

    # Name the exact lines still unseen. A vague "read on" is what produces the
    # skipped gap the model then reports as missing code.
    unread = context.unread_ranges(source_file.path, total)
    if unread:
        spans = ", ".join(f"{low}-{high}" for low, high in unread)
        payload["unread_lines"] = spans
        payload["note"] = (
            f"You have not seen lines {spans} of this file. Call read_file with "
            f"start_line={unread[0][0]} to continue before concluding anything is absent."
        )
    if redactions:
        payload["redacted_secrets"] = redactions

    return payload, f"{path}:{start}-{end}"


def _find_symbol(context: ToolContext, arguments: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Look a symbol up in indexed chunk metadata.

    Queries the chunks the indexer already produced — the repository is never
    re-parsed for a tool call.
    """
    name = _require_string(arguments, "name")[:200]
    limit = _bounded_int(arguments.get("limit", 5), 1, MAX_SYMBOL_RESULTS)

    # LIKE wildcards in a symbol name are literal characters, not patterns.
    escaped = name.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    stmt = (
        select(CodeChunk, SourceFile.path)
        .join(SourceFile, SourceFile.id == CodeChunk.file_id)
        .where(
            CodeChunk.repository_id == context.repository.id,
            or_(
                func.lower(CodeChunk.symbol).like(pattern, escape="\\"),
                func.lower(CodeChunk.parent_symbol).like(pattern, escape="\\"),
            ),
        )
        # Exact matches first, then shorter names: a search for "create_session"
        # should not be buried under "create_session_token_for_user".
        .order_by(
            (func.lower(CodeChunk.symbol) == name.lower()).desc(),
            func.length(func.coalesce(CodeChunk.symbol, "")),
        )
        .limit(limit)
    )

    matches = []
    for chunk, path in context.session.execute(stmt):
        excerpt, truncated = _excerpt(chunk.content)
        matches.append(
            {
                "id": context.cite(
                    path=path,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    symbol=chunk.symbol,
                    origin=FIND_SYMBOL,
                ),
                "path": path,
                "symbol": chunk.symbol,
                "parent_symbol": chunk.parent_symbol,
                "kind": str(chunk.chunk_type),
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "content": excerpt,
                "content_truncated": truncated,
            }
        )

    if not matches:
        return ({"results": [], "note": f"No indexed symbol matching '{name}'."}, f"{name} → 0")

    return ({"results": matches}, f"{name} → {len(matches)}")


# ---- argument validation --------------------------------------------------


def _excerpt(content: str) -> tuple[str, bool]:
    redacted, _ = redact_secrets(content)
    if len(redacted) <= _MAX_EXCERPT_CHARS:
        return redacted, False
    cut = redacted.rfind("\n", 0, _MAX_EXCERPT_CHARS)
    return redacted[: cut if cut > 0 else _MAX_EXCERPT_CHARS], True


def _require_string(arguments: dict[str, Any], key: str) -> str:
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object.")
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' must be a non-empty string.")
    return value.strip()


def _bounded_int(value: Any, low: int, high: int) -> int:
    """Clamp rather than reject.

    A slightly out-of-range number from the model is not worth failing a tool
    call over; the bound is what matters, not punishing the request.
    """
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("expected an integer") from exc
    return max(low, min(number, high))


def new_context(session: Session, *, repository: Repository, settings: Settings) -> ToolContext:
    return ToolContext(session=session, repository=repository, settings=settings)


def summarise_activity(activity: list[ToolActivity]) -> list[dict[str, Any]]:
    """Shape activity for persistence and display."""
    return [
        {"tool": item.tool, "duration_ms": item.duration_ms, "ok": item.ok, "detail": item.detail}
        for item in activity
    ]


__all__ = [
    "EvidenceRecord",
    "ToolActivity",
    "ToolContext",
    "execute",
    "new_context",
    "summarise_activity",
]
