"""Runs the tools the model asks for.

The backend owns execution entirely: it validates every argument, scopes every
lookup to the one repository under investigation, and bounds every result. The
model chooses *which* tool and *what* to look for; it never chooses what the
tool is allowed to reach.
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
from app.services.llm import ToolCall, ToolResult
from app.services.tools.registry import (
    FIND_SYMBOL,
    MAX_READ_LINES,
    MAX_SEARCH_RESULTS,
    MAX_SYMBOL_RESULTS,
    READ_FILE,
    SEARCH_CODE,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ToolActivity:
    """A bounded record of one tool call, safe to persist and to show a user.

    Carries no file contents and no arguments — only what happened, so the UI
    can say "Searching codebase…" and a reviewer can see the shape of the
    investigation without exposing reasoning or source.
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
        if context.output_chars_used >= context.settings.agent_max_tool_output_chars:
            raise ToolBudgetExceeded("Tool output budget exhausted.")

        if call.name == SEARCH_CODE:
            payload, detail = _search_code(context, call.arguments)
        elif call.name == READ_FILE:
            payload, detail = _read_file(context, call.arguments)
        elif call.name == FIND_SYMBOL:
            payload, detail = _find_symbol(context, call.arguments)
        else:
            # The model asked for a tool that does not exist.
            ok = False
            payload = {"error": f"Unknown tool '{call.name}'."}
            detail = call.name

        content = json.dumps(payload, ensure_ascii=False)
        context.output_chars_used += len(content)

    except ToolBudgetExceeded as exc:
        ok = False
        content = json.dumps({"error": str(exc)})
    except (KeyError, TypeError, ValueError) as exc:
        # Malformed arguments from the model.
        ok = False
        content = json.dumps({"error": f"Invalid arguments: {exc}"})
    except Exception:
        # A genuine backend fault. Logged with a traceback; the model is told
        # only that the tool failed, so nothing internal leaks into context.
        logger.exception("Tool %s failed", call.name)
        ok = False
        content = json.dumps({"error": "The tool failed to run."})

    duration_ms = int((time.monotonic() - started) * 1000)
    context.activity.append(
        ToolActivity(tool=call.name, duration_ms=duration_ms, ok=ok, detail=detail)
    )
    logger.info("Tool %s ok=%s duration_ms=%d", call.name, ok, duration_ms)

    return ToolResult(tool_use_id=call.id, content=content, is_error=not ok)


# ---- tools ----------------------------------------------------------------


def _search_code(context: ToolContext, arguments: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Semantic search, reusing the one retrieval implementation."""
    query = _require_string(arguments, "query")
    limit = _bounded_int(arguments.get("limit", 5), 1, MAX_SEARCH_RESULTS)

    result = retrieval_service.retrieve(
        context.session,
        repository=context.repository,
        query=query,
        top_k=limit,
        settings=context.settings,
    )

    return (
        {
            "results": [
                {
                    "path": source.file_path,
                    "symbol": source.qualified_symbol,
                    "kind": source.chunk_type,
                    "start_line": source.start_line,
                    "end_line": source.end_line,
                    "language": source.language,
                    "score": round(source.score, 4),
                    "content": source.content,
                }
                for source in result.sources
            ]
        },
        f"{len(result.sources)} results",
    )


def _read_file(context: ToolContext, arguments: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Read a bounded line range from one indexed file.

    Lookup is by exact path within this repository's rows. There is no
    filesystem access, so a path like ``../../etc/passwd`` or ``C:\\Windows``
    simply does not match a row — traversal is impossible by construction
    rather than by filtering.
    """
    path = _require_string(arguments, "path")

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
            path,
        )

    lines = source_file.content.splitlines()
    total = len(lines)

    start = _bounded_int(arguments.get("start_line", 1), 1, max(total, 1))
    requested_end = arguments.get("end_line")
    end = _bounded_int(requested_end, start, total) if requested_end is not None else total
    # Always cap the window, however wide a range was asked for.
    end = min(end, start + MAX_READ_LINES - 1, total)

    excerpt = "\n".join(lines[start - 1 : end])

    # Remember the whole file, not the excerpt: patch validation matches
    # old_text against full content, and the model may read several ranges.
    context.files_read[source_file.path] = source_file.content

    return (
        {
            "path": source_file.path,
            "language": source_file.language,
            "start_line": start,
            "end_line": end,
            "total_lines": total,
            "truncated": end < total,
            "content": excerpt,
        },
        f"{path}:{start}-{end}",
    )


def _find_symbol(context: ToolContext, arguments: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Look a symbol up in indexed chunk metadata.

    Queries the chunks the indexer already produced — the repository is never
    re-parsed for a tool call.
    """
    name = _require_string(arguments, "name")
    limit = _bounded_int(arguments.get("limit", 5), 1, MAX_SYMBOL_RESULTS)

    pattern = f"%{name.lower()}%"
    stmt = (
        select(CodeChunk, SourceFile.path)
        .join(SourceFile, SourceFile.id == CodeChunk.file_id)
        .where(
            CodeChunk.repository_id == context.repository.id,
            or_(
                func.lower(CodeChunk.symbol).like(pattern),
                func.lower(CodeChunk.parent_symbol).like(pattern),
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

    matches = [
        {
            "path": path,
            "symbol": chunk.symbol,
            "parent_symbol": chunk.parent_symbol,
            "kind": str(chunk.chunk_type),
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "language": chunk.language,
            "content": chunk.content,
        }
        for chunk, path in context.session.execute(stmt)
    ]

    if not matches:
        return ({"results": [], "note": f"No indexed symbol matching '{name}'."}, name)

    return ({"results": matches}, f"{name} ({len(matches)})")


# ---- argument validation --------------------------------------------------


def _require_string(arguments: dict[str, Any], key: str) -> str:
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
    "ToolActivity",
    "ToolContext",
    "execute",
    "new_context",
    "summarise_activity",
]
