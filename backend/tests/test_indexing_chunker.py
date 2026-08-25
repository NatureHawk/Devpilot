"""Chunking strategy: structure first, line windows only as a fallback."""

from __future__ import annotations

from itertools import pairwise

import pytest

from app.services.indexing.chunker import ChunkDraft, ChunkingLimits, chunk_file
from app.services.indexing.parser import parse_file

GENEROUS = ChunkingLimits(max_chunk_chars=8_000, fallback_chunk_lines=120)
# Smaller than the 123-character UserService class, larger than either of its
# methods — so the class must split and the methods must survive whole.
TIGHT = ChunkingLimits(max_chunk_chars=100, fallback_chunk_lines=120)

SOURCE = b"""import os

CONSTANT = 1


class UserService:
    def create_user(self, name):
        return name

    def delete_user(self, uid):
        return uid


def helper(x):
    return x * 2
"""


def chunks(source: bytes, language: str | None, limits: ChunkingLimits) -> list[ChunkDraft]:
    parsed = parse_file(source, language) if language else None
    return chunk_file(source, parsed, limits=limits)


def by_symbol(drafts: list[ChunkDraft]) -> dict[str | None, ChunkDraft]:
    return {draft.symbol: draft for draft in drafts}


class TestStructuralChunking:
    def test_a_top_level_function_becomes_its_own_chunk(self) -> None:
        found = by_symbol(chunks(SOURCE, "python", GENEROUS))

        assert found["helper"].chunk_type == "function"
        assert "def helper" in found["helper"].content

    def test_module_level_code_is_kept_as_a_module_chunk(self) -> None:
        drafts = chunks(SOURCE, "python", GENEROUS)
        module = next(d for d in drafts if d.chunk_type == "module")

        assert "import os" in module.content

    def test_a_class_that_fits_is_kept_whole_and_not_duplicated(self) -> None:
        """Emitting the class and its methods would store the same code twice."""
        drafts = chunks(SOURCE, "python", GENEROUS)
        types = [d.chunk_type for d in drafts]

        assert types.count("class") == 1
        assert "method" not in types

    def test_an_oversized_class_yields_its_methods_with_parent_context(self) -> None:
        drafts = chunks(SOURCE, "python", TIGHT)
        methods = [d for d in drafts if d.chunk_type == "method"]

        assert {d.symbol for d in methods} == {"create_user", "delete_user"}
        assert all(d.parent_symbol == "UserService" for d in methods)

    def test_an_oversized_class_is_not_duplicated_into_its_methods(self) -> None:
        drafts = chunks(SOURCE, "python", TIGHT)
        create = next(d for d in drafts if d.symbol == "create_user")

        assert "delete_user" not in create.content

    def test_line_ranges_are_one_based_and_inclusive(self) -> None:
        found = by_symbol(chunks(SOURCE, "python", GENEROUS))
        helper = found["helper"]

        lines = SOURCE.decode().splitlines()
        assert lines[helper.start_line - 1].startswith("def helper")
        assert helper.end_line >= helper.start_line


class TestOversizedSymbols:
    @staticmethod
    def big_function() -> bytes:
        body = "\n".join(f"    value_{i} = {i}" for i in range(80))
        return f"def enormous():\n{body}\n".encode()

    def test_a_symbol_too_large_to_store_is_split_into_parts(self) -> None:
        drafts = chunks(self.big_function(), "python", ChunkingLimits(200, 1_000))

        assert len(drafts) > 1
        assert all(d.part_count == len(drafts) for d in drafts)
        assert [d.part_index for d in drafts] == list(range(1, len(drafts) + 1))

    def test_every_part_keeps_the_symbol_identity(self) -> None:
        drafts = chunks(self.big_function(), "python", ChunkingLimits(200, 1_000))

        assert all(d.symbol == "enormous" for d in drafts)
        assert all(d.chunk_type == "function" for d in drafts)

    def test_parts_do_not_cut_through_a_line(self) -> None:
        source = self.big_function()
        drafts = chunks(source, "python", ChunkingLimits(200, 1_000))

        for draft in drafts[:-1]:
            # A window always ends just past a newline.
            assert source[draft.end_byte - 1 : draft.end_byte] == b"\n"


class TestFallbackChunking:
    def test_a_file_with_no_parser_is_still_chunked(self) -> None:
        source = b"# Title\n\nProse paragraph.\n" * 20
        drafts = chunk_file(source, None, limits=ChunkingLimits(400, 10))

        assert drafts
        assert {d.chunk_type for d in drafts} == {"block"}

    def test_fallback_respects_the_line_window(self) -> None:
        source = b"".join(f"line {i}\n".encode() for i in range(50))
        drafts = chunk_file(source, None, limits=ChunkingLimits(10_000, 10))

        assert all(d.end_line - d.start_line + 1 <= 10 for d in drafts)

    def test_a_parseable_file_with_no_symbols_falls_back(self) -> None:
        source = b"x = 1\ny = 2\nprint(x + y)\n"
        drafts = chunk_file(source, parse_file(source, "python"), limits=GENEROUS)

        assert drafts
        assert drafts[0].content.strip().startswith("x = 1")

    def test_empty_source_produces_nothing(self) -> None:
        assert chunk_file(b"", None, limits=GENEROUS) == []


class TestCoverageAndOverlap:
    @pytest.mark.parametrize("limits", [GENEROUS, TIGHT, ChunkingLimits(60, 5)])
    def test_chunks_never_overlap(self, limits: ChunkingLimits) -> None:
        spans = sorted((d.start_byte, d.end_byte) for d in chunks(SOURCE, "python", limits))

        for (_, end), (next_start, _) in pairwise(spans):
            assert end <= next_start

    def test_all_meaningful_source_is_covered(self) -> None:
        drafts = chunks(SOURCE, "python", TIGHT)
        joined = "".join(d.content for d in drafts)

        for token in ("import os", "CONSTANT", "create_user", "delete_user", "helper"):
            assert token in joined

    def test_punctuation_only_fragments_are_not_stored(self) -> None:
        """Leftovers like a lone brace add noise without adding meaning."""
        source = b"export const f = () => 1;\n"
        drafts = chunk_file(source, parse_file(source, "typescript"), limits=GENEROUS)

        assert all(any(c.isalnum() for c in d.content) for d in drafts)
