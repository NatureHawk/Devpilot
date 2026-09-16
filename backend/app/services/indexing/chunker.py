"""Turns a parsed file into storable chunks.

The strategy, in one sentence: emit the largest syntactic unit that fits inside
the size limit, and descend into it only when it does not.

That gives whole functions and methods as chunks, a class kept intact when it is
small, and — when a class is too large — its methods individually plus the
class's own leftover body, never both. Nothing is duplicated, and every byte of
a file that carries content is covered exactly once.

Fixed-size splitting is the last resort, used only for a single symbol that
exceeds the limit on its own and for files with no parseable structure.

Trivia between declarations
---------------------------
The regions between declarations ("gaps") often hold no code at all: a divider
comment such as ``# --- PAGE 2: COMMERCIAL ---``, or the ``;`` that closes
``const Page = () => {...};``. Stored on their own they become retrieval units
whose embedded text is almost entirely the path/language header, which sits
close to *every* question and outranks real code. So gap trivia is attached to
the declaration it belongs to, by structure rather than by size:

- a comment block directly above a declaration is part of that declaration
  (it is the declaration's heading or documentation);
- punctuation, and the rest of the line a declaration ends on, belongs to the
  declaration before it.

Only trivia moves. Code in a gap (imports, constants, setup) stays its own
module chunk, two declarations are never merged into one chunk, a merge that
would exceed the size limit is not made, and byte ranges stay contiguous, so
source is neither duplicated nor dropped.
"""

from __future__ import annotations

import bisect
import enum
from dataclasses import dataclass, replace

from app.services.indexing.parser import ParsedFile, SymbolNode

# Markers that start a comment line, per parseable language. A line beginning
# with one of these (after indentation) carries no code of its own. `*` covers
# the continuation lines of a /** ... */ block.
_JS_COMMENT_PREFIXES = ("//", "/*", "*/", "*")
_COMMENT_PREFIXES: dict[str, tuple[str, ...]] = {
    "python": ("#",),
    "javascript": _JS_COMMENT_PREFIXES,
    "jsx": _JS_COMMENT_PREFIXES,
    "typescript": _JS_COMMENT_PREFIXES,
    "tsx": _JS_COMMENT_PREFIXES,
}


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


@dataclass(frozen=True, slots=True)
class _Gap:
    """A region no symbol claims, resolved once its neighbours are known."""

    start_byte: int
    end_byte: int
    parent_symbol: str | None


class _LineKind(enum.Enum):
    BLANK = "blank"
    COMMENT = "comment"
    PUNCTUATION = "punctuation"
    CODE = "code"


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

    # Symbols and gaps in source order. Gaps are resolved afterwards, because
    # where a gap's trivia belongs depends on the chunks either side of it.
    items: list[ChunkDraft | _Gap] = []
    cursor = 0
    for root in roots:
        # Content between the previous symbol and this one: imports, constants,
        # module docstrings, divider comments.
        _add_gap(items, cursor, root.symbol.start_byte, parent_symbol=None)
        _emit_symbol(source, line_starts, root, limits, items)
        cursor = max(cursor, root.symbol.end_byte)

    _add_gap(items, cursor, len(source), parent_symbol=None)

    drafts = _resolve_gaps(
        source,
        line_starts,
        items,
        limits,
        comment_prefixes=_COMMENT_PREFIXES.get(parsed.language, ()),
    )
    drafts.sort(key=lambda draft: (draft.start_byte, draft.part_index))
    return drafts


def is_trivia_only(text: str, language: str) -> bool:
    """Whether ``text`` holds only comments, punctuation and whitespace.

    Used by retrieval to recognise low-value chunks in an index built before
    trivia was attached to declarations.
    """
    prefixes = _COMMENT_PREFIXES.get(language, ())
    return all(_classify(line, prefixes) is not _LineKind.CODE for line in text.splitlines())


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


def _add_gap(
    items: list[ChunkDraft | _Gap], start: int, end: int, *, parent_symbol: str | None
) -> None:
    if end > start:
        items.append(_Gap(start_byte=start, end_byte=end, parent_symbol=parent_symbol))


def _emit_symbol(
    source: bytes,
    line_starts: list[int],
    node: _SymbolTreeNode,
    limits: ChunkingLimits,
    out: list[ChunkDraft | _Gap],
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
        _add_gap(out, cursor, child.symbol.start_byte, parent_symbol=symbol.name)
        _emit_symbol(source, line_starts, child, limits, out)
        cursor = max(cursor, child.symbol.end_byte)

    _add_gap(out, cursor, symbol.end_byte, parent_symbol=symbol.name)


def _emit_split_symbol(
    source: bytes,
    line_starts: list[int],
    symbol: SymbolNode,
    limits: ChunkingLimits,
    out: list[ChunkDraft | _Gap],
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


# ---- gap resolution -------------------------------------------------------


def _resolve_gaps(
    source: bytes,
    line_starts: list[int],
    items: list[ChunkDraft | _Gap],
    limits: ChunkingLimits,
    *,
    comment_prefixes: tuple[str, ...],
) -> list[ChunkDraft]:
    """Attach gap trivia to neighbouring declarations; chunk what remains.

    Walks items in source order. A neighbour only counts when it is directly
    adjacent (its byte range touches the gap), which keeps attachment inside one
    scope: a gap in a class body can only reach that class's own members.
    """
    resolved: list[ChunkDraft] = []

    for index, item in enumerate(items):
        if isinstance(item, ChunkDraft):
            resolved.append(item)
            continue

        gap = item
        lead_end, trail_start = _split_gap(source, line_starts, gap, comment_prefixes)
        middle_start, middle_end = gap.start_byte, gap.end_byte

        previous = resolved[-1] if resolved and resolved[-1].end_byte == gap.start_byte else None
        following_item = items[index + 1] if index + 1 < len(items) else None
        following = (
            following_item
            if isinstance(following_item, ChunkDraft)
            and following_item.start_byte == gap.end_byte
            # A continuation part is the middle of a symbol, not its heading.
            and following_item.part_index == 1
            else None
        )

        # Trailing punctuation and the rest of the previous line -> previous.
        if previous is not None and lead_end > gap.start_byte:
            extended = _extend(source, line_starts, previous, end=lead_end)
            if len(extended.content) <= limits.max_chunk_chars:
                resolved[-1] = previous = extended
                middle_start = lead_end

        # Comment block introducing the next declaration -> that declaration.
        if trail_start < gap.end_byte:
            attached = False
            if following is not None:
                extended = _extend(source, line_starts, following, start=trail_start)
                if len(extended.content) <= limits.max_chunk_chars:
                    items[index + 1] = extended
                    middle_end = trail_start
                    attached = True

            # Nothing follows to introduce (end of file or scope): a comment-only
            # remainder is still trivia, so it closes the previous declaration
            # rather than standing alone.
            if (
                not attached
                and previous is not None
                and previous.end_byte == middle_start
                and not _has_content(_slice_text(source, middle_start, trail_start))
            ):
                extended = _extend(source, line_starts, previous, end=gap.end_byte)
                if len(extended.content) <= limits.max_chunk_chars:
                    resolved[-1] = extended
                    middle_start = middle_end = gap.end_byte

        _emit_gap_chunks(
            source,
            line_starts,
            middle_start,
            middle_end,
            limits,
            resolved,
            parent_symbol=gap.parent_symbol,
        )

    return resolved


def _split_gap(
    source: bytes,
    line_starts: list[int],
    gap: _Gap,
    comment_prefixes: tuple[str, ...],
) -> tuple[int, int]:
    """Find the trivia at each end of a gap.

    Returns ``(lead_end, trail_start)``:

    - ``[gap.start, lead_end)`` is trailing trivia of the preceding code: the
      rest of the line a declaration ended on, then punctuation-only lines.
      Blank lines extend the scan but are never claimed on their own.
    - ``[trail_start, gap.end)`` is the comment block directly above the next
      declaration, including blank lines between it and the declaration.

    ``lead_end == gap.start`` / ``trail_start == gap.end`` mean "none".
    """
    segments = _line_segments(source, line_starts, gap.start_byte, gap.end_byte)
    starts_mid_line = gap.start_byte != _line_start_before(line_starts, gap.start_byte)

    lead_end = gap.start_byte
    for position, (start, end) in enumerate(segments):
        kind = _classify(_slice_text(source, start, end), comment_prefixes)
        if position == 0 and starts_mid_line:
            if kind is _LineKind.CODE:
                break
            if kind is not _LineKind.BLANK:
                lead_end = end
            continue
        if kind is _LineKind.PUNCTUATION:
            lead_end = end
        elif kind is not _LineKind.BLANK:
            break

    trail_start = gap.end_byte
    for position in range(len(segments) - 1, -1, -1):
        if position == 0 and starts_mid_line:
            break
        start, end = segments[position]
        if start < lead_end:
            break
        kind = _classify(_slice_text(source, start, end), comment_prefixes)
        if kind is _LineKind.COMMENT:
            trail_start = start
        elif kind is not _LineKind.BLANK:
            break

    return lead_end, trail_start


def _emit_gap_chunks(
    source: bytes,
    line_starts: list[int],
    start: int,
    end: int,
    limits: ChunkingLimits,
    out: list[ChunkDraft],
    *,
    parent_symbol: str | None,
) -> None:
    """Chunk what is left of a gap after trivia has been attached.

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


def _extend(
    source: bytes,
    line_starts: list[int],
    draft: ChunkDraft,
    *,
    start: int | None = None,
    end: int | None = None,
) -> ChunkDraft:
    """Widen a draft's byte range, re-deriving content and lines from source."""
    new_start = draft.start_byte if start is None else start
    new_end = draft.end_byte if end is None else end
    return replace(
        draft,
        start_byte=new_start,
        end_byte=new_end,
        start_line=_line_at(line_starts, new_start),
        end_line=_line_at(line_starts, max(new_start, new_end - 1)),
        content=_slice_text(source, new_start, new_end),
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


def _classify(line: str, comment_prefixes: tuple[str, ...]) -> _LineKind:
    stripped = line.strip()
    if not stripped:
        return _LineKind.BLANK
    if comment_prefixes and stripped.startswith(comment_prefixes):
        return _LineKind.COMMENT
    if not any(character.isalnum() for character in stripped):
        return _LineKind.PUNCTUATION
    return _LineKind.CODE


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


def _line_start_before(line_starts: list[int], offset: int) -> int:
    """Byte offset of the start of the line containing ``offset``."""
    return line_starts[bisect.bisect_right(line_starts, offset) - 1]


def _line_segments(
    source: bytes, line_starts: list[int], start: int, end: int
) -> list[tuple[int, int]]:
    """Split ``[start, end)`` into per-line byte ranges (first may start mid-line)."""
    segments: list[tuple[int, int]] = []
    cursor = start
    index = bisect.bisect_right(line_starts, start)
    while cursor < end:
        line_end = line_starts[index] if index < len(line_starts) else len(source)
        segment_end = min(line_end, end)
        segments.append((cursor, segment_end))
        cursor = segment_end
        index += 1
    return segments


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
