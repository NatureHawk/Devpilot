"""Turns a parsed file into storable chunks.

The strategy, in one sentence: emit the largest syntactic unit that fits inside
the size limit, and descend into it only when it does not.

That gives whole functions and methods as chunks, a class kept intact when it is
small, and — when a class is too large — its methods individually plus the
class's own leftover body, never both. Nothing is duplicated, and every byte of
a file that carries content is covered exactly once.

Fixed-size splitting is the last resort, used only for a single symbol that
exceeds the limit on its own and for files with no parseable structure.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from app.services.indexing.parser import ParsedFile, SymbolNode


@dataclass(frozen=True, slots=True)
class ChunkingLimits:
    max_chunk_chars: int
    fallback_chunk_lines: int


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """A chunk before it becomes a database row."""

    chunk_type: str
    node_type: str | None
    symbol: str | None
    parent_symbol: str | None
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    content: str
    part_index: int = 1
    part_count: int = 1


@dataclass(slots=True)
class _SymbolTreeNode:
    symbol: SymbolNode
    children: list[_SymbolTreeNode]


def chunk_file(
    source: bytes,
    parsed: ParsedFile | None,
    *,
    limits: ChunkingLimits,
) -> list[ChunkDraft]:
    """Produce chunks for one file.

    ``parsed`` is None for a language with no grammar, which falls back to line
    windows — the file is still indexed, just without structure.
    """
    if not source:
        return []

    line_starts = _line_start_offsets(source)

    if parsed is None or not parsed.symbols:
        return _fallback_chunks(source, line_starts, limits=limits, parent_symbol=None)

    roots = _build_symbol_tree(parsed.symbols)

    drafts: list[ChunkDraft] = []
    cursor = 0
    for root in roots:
        # Content between the previous symbol and this one: imports, constants,
        # module docstrings. Real code, so it is chunked rather than dropped.
        _emit_gap(source, line_starts, cursor, root.symbol.start_byte, limits, drafts)
        _emit_symbol(source, line_starts, root, limits, drafts)
        cursor = max(cursor, root.symbol.end_byte)

    _emit_gap(source, line_starts, cursor, len(source), limits, drafts)

    drafts.sort(key=lambda draft: (draft.start_byte, draft.part_index))
    return drafts


# ---- symbol tree ----------------------------------------------------------


def _build_symbol_tree(symbols: list[SymbolNode]) -> list[_SymbolTreeNode]:
    """Rebuild containment from flat, source-ordered symbols using byte ranges."""
    roots: list[_SymbolTreeNode] = []
    stack: list[_SymbolTreeNode] = []

    for symbol in symbols:
        node = _SymbolTreeNode(symbol=symbol, children=[])

        # Unwind to the innermost symbol that still encloses this one.
        while stack and stack[-1].symbol.end_byte <= symbol.start_byte:
            stack.pop()

        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
        stack.append(node)

    return roots


# ---- emission -------------------------------------------------------------


def _emit_symbol(
    source: bytes,
    line_starts: list[int],
    node: _SymbolTreeNode,
    limits: ChunkingLimits,
    out: list[ChunkDraft],
) -> None:
    symbol = node.symbol
    text = _slice_text(source, symbol.start_byte, symbol.end_byte)

    # Fits whole: keep it intact, children included. This is the common case and
    # the one that produces the most useful chunk.
    if len(text) <= limits.max_chunk_chars:
        if text.strip():
            out.append(
                ChunkDraft(
                    chunk_type=symbol.chunk_type,
                    node_type=symbol.node_type,
                    symbol=symbol.name,
                    parent_symbol=symbol.parent_name,
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                    start_byte=symbol.start_byte,
                    end_byte=symbol.end_byte,
                    content=text,
                )
            )
        return

    # Too large and indivisible: split on line boundaries, keeping the symbol's
    # identity on every part so context survives.
    if not node.children:
        _emit_split_symbol(source, line_starts, symbol, limits, out)
        return

    # Too large but structured: emit the children, and the parent's own leftover
    # body as separate blocks. The parent is never duplicated into its children.
    cursor = symbol.start_byte
    for child in node.children:
        _emit_gap(
            source,
            line_starts,
            cursor,
            child.symbol.start_byte,
            limits,
            out,
            parent_symbol=symbol.name,
        )
        _emit_symbol(source, line_starts, child, limits, out)
        cursor = max(cursor, child.symbol.end_byte)

    _emit_gap(source, line_starts, cursor, symbol.end_byte, limits, out, parent_symbol=symbol.name)


def _emit_split_symbol(
    source: bytes,
    line_starts: list[int],
    symbol: SymbolNode,
    limits: ChunkingLimits,
    out: list[ChunkDraft],
) -> None:
    """Split one oversized symbol into line-aligned parts."""
    windows = _line_windows(source, line_starts, symbol.start_byte, symbol.end_byte, limits)
    windows = [window for window in windows if _slice_text(source, *window).strip()]
    if not windows:
        return

    total = len(windows)
    for index, (start, end) in enumerate(windows, start=1):
        out.append(
            ChunkDraft(
                chunk_type=symbol.chunk_type,
                node_type=symbol.node_type,
                symbol=symbol.name,
                parent_symbol=symbol.parent_name,
                start_line=_line_at(line_starts, start),
                end_line=_line_at(line_starts, max(start, end - 1)),
                start_byte=start,
                end_byte=end,
                content=_slice_text(source, start, end),
                part_index=index,
                part_count=total,
            )
        )


def _emit_gap(
    source: bytes,
    line_starts: list[int],
    start: int,
    end: int,
    limits: ChunkingLimits,
    out: list[ChunkDraft],
    *,
    parent_symbol: str | None = None,
) -> None:
    """Chunk a region that no symbol claims.

    At the top level this is module-level code; inside a symbol it is the
    parent's own body around its children.
    """
    if end <= start or not _has_content(_slice_text(source, start, end)):
        return

    chunk_type = "block" if parent_symbol else "module"
    for window_start, window_end in _line_windows(source, line_starts, start, end, limits):
        text = _slice_text(source, window_start, window_end)
        if not _has_content(text):
            continue
        out.append(
            ChunkDraft(
                chunk_type=chunk_type,
                node_type=None,
                symbol=parent_symbol,
                parent_symbol=None,
                start_line=_line_at(line_starts, window_start),
                end_line=_line_at(line_starts, max(window_start, window_end - 1)),
                start_byte=window_start,
                end_byte=window_end,
                content=text,
            )
        )


def _fallback_chunks(
    source: bytes,
    line_starts: list[int],
    *,
    limits: ChunkingLimits,
    parent_symbol: str | None,
) -> list[ChunkDraft]:
    """Line-window chunks for a file with no usable structure."""
    drafts: list[ChunkDraft] = []
    for start, end in _line_windows(source, line_starts, 0, len(source), limits):
        text = _slice_text(source, start, end)
        if not text.strip():
            continue
        drafts.append(
            ChunkDraft(
                chunk_type="block",
                node_type=None,
                symbol=parent_symbol,
                parent_symbol=None,
                start_line=_line_at(line_starts, start),
                end_line=_line_at(line_starts, max(start, end - 1)),
                start_byte=start,
                end_byte=end,
                content=text,
            )
        )
    return drafts


# ---- byte/line helpers ----------------------------------------------------


def _has_content(text: str) -> bool:
    """Whether a region is worth storing as its own chunk.

    Requires at least one alphanumeric character: leftovers like a lone ``;`` or
    a closing brace carry no meaning on their own and only add noise to search.
    """
    return any(character.isalnum() for character in text)


def _line_start_offsets(source: bytes) -> list[int]:
    """Byte offset of the start of every line, for O(log n) line lookups."""
    offsets = [0]
    offsets.extend(index + 1 for index, byte in enumerate(source) if byte == 0x0A)
    return offsets


def _line_at(line_starts: list[int], byte_offset: int) -> int:
    """1-based line number containing a byte offset."""
    return bisect.bisect_right(line_starts, byte_offset)


def _slice_text(source: bytes, start: int, end: int) -> str:
    # errors="replace" only ever applies to a slice that split a multi-byte
    # character, which the line-aligned windows below avoid; it is a guard, not
    # an expectation.
    return source[start:end].decode("utf-8", errors="replace")


def _line_windows(
    source: bytes,
    line_starts: list[int],
    start: int,
    end: int,
    limits: ChunkingLimits,
) -> list[tuple[int, int]]:
    """Split a byte range into line-aligned windows within the size limits.

    Windows never cut mid-line, so a chunk boundary always lands somewhere a
    reader would accept, and never inside a multi-byte character.
    """
    first_line = _line_at(line_starts, start) - 1
    last_line = _line_at(line_starts, max(start, end - 1)) - 1

    windows: list[tuple[int, int]] = []
    window_start = start
    lines_in_window = 0

    for line_index in range(first_line, last_line + 1):
        line_end = line_starts[line_index + 1] if line_index + 1 < len(line_starts) else len(source)
        line_end = min(line_end, end)
        lines_in_window += 1

        too_many_lines = lines_in_window >= limits.fallback_chunk_lines
        too_many_chars = (line_end - window_start) >= limits.max_chunk_chars

        if (too_many_lines or too_many_chars) and line_end > window_start:
            windows.append((window_start, line_end))
            window_start = line_end
            lines_in_window = 0

    if window_start < end:
        windows.append((window_start, end))

    return windows
