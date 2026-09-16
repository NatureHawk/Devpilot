"""Gap trivia belongs to declarations, not to retrieval units of its own.

A divider comment like ``# --- 2. DEMO SIMULATION ---`` stored alone embeds as
little more than its path header and outranks real code for every question.
These tests pin the structural rules that prevent that, and the invariants
that must survive them: exact byte coverage, no duplication, exact line ranges.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from app.services.indexing.chunker import ChunkDraft, ChunkingLimits, chunk_file, is_trivia_only
from app.services.indexing.parser import parse_file

GENEROUS = ChunkingLimits(max_chunk_chars=8_000, fallback_chunk_lines=120)


def chunks(source: bytes, language: str, limits: ChunkingLimits = GENEROUS) -> list[ChunkDraft]:
    return chunk_file(source, parse_file(source, language), limits=limits)


def by_symbol(drafts: list[ChunkDraft]) -> dict[str | None, ChunkDraft]:
    return {draft.symbol: draft for draft in drafts}


def assert_exact_coverage(source: bytes, drafts: list[ChunkDraft]) -> None:
    """No overlap, content equals its byte range, lines match bytes, nothing meaningful lost."""
    spans = sorted((d.start_byte, d.end_byte) for d in drafts)
    for (_, end), (next_start, _) in pairwise(spans):
        assert end <= next_start, "chunks overlap"

    covered = bytearray(len(source))
    for draft in drafts:
        assert draft.content == source[draft.start_byte : draft.end_byte].decode()
        assert draft.start_line == source[: draft.start_byte].count(b"\n") + 1
        assert (
            draft.end_line == source[: max(draft.start_byte, draft.end_byte - 1)].count(b"\n") + 1
        )
        for index in range(draft.start_byte, draft.end_byte):
            assert not covered[index], "source byte stored twice"
            covered[index] = 1

    for index, byte in enumerate(source):
        character = chr(byte)
        if character.isalnum() or character in "#/":
            assert covered[index], f"meaningful byte {index} ({character!r}) was dropped"


class TestDividerComments:
    def test_divider_before_a_function_becomes_part_of_that_function(self) -> None:
        source = (
            b"import os\n\n\n"
            b"# --- 2. DEMO SIMULATION ---\n"
            b"def simulate_sale():\n"
            b"    return os.getpid()\n"
        )
        drafts = chunks(source, "python")
        found = by_symbol(drafts)

        simulate = found["simulate_sale"]
        assert simulate.content.startswith("# --- 2. DEMO SIMULATION ---\ndef simulate_sale")
        assert simulate.start_line == 4
        assert simulate.chunk_type == "function"
        assert "DEMO SIMULATION" not in found[None].content
        assert not any(is_trivia_only(d.content, "python") for d in drafts)
        assert_exact_coverage(source, drafts)

    def test_divider_before_a_class_becomes_part_of_that_class(self) -> None:
        source = b"x = 1\n\n# --- UPDATED DATA MODEL ---\nclass Order:\n    transaction_id: str\n"
        drafts = chunks(source, "python")
        order = by_symbol(drafts)["Order"]

        assert order.chunk_type == "class"
        assert order.content.startswith("# --- UPDATED DATA MODEL ---\nclass Order")
        assert order.start_line == 3
        assert_exact_coverage(source, drafts)

    def test_a_meaningful_module_chunk_stays_independent(self) -> None:
        """Imports and setup are code, not trivia, and are never folded into a function."""
        source = (
            b"import os\n"
            b"BASE_DIR = os.path.dirname(__file__)\n\n"
            b"def get_db():\n"
            b"    return BASE_DIR\n"
        )
        drafts = chunks(source, "python")
        found = by_symbol(drafts)

        assert found[None].chunk_type == "module"
        assert "BASE_DIR = os.path" in found[None].content
        assert found["get_db"].content.startswith("def get_db")
        assert_exact_coverage(source, drafts)

    def test_a_comment_introducing_module_code_stays_with_that_code(self) -> None:
        source = (
            b"def a():\n    return 1\n\n"
            b"# --- PAGE 3: OPERATIONS ---\n"
            b"COLORS = {'Food': 1}\n\n"
            b"def b():\n    return COLORS\n"
        )
        drafts = chunks(source, "python")
        module = by_symbol(drafts)[None]

        assert module.content.lstrip().startswith("# --- PAGE 3: OPERATIONS ---\nCOLORS")
        assert by_symbol(drafts)["b"].content.startswith("def b")
        assert_exact_coverage(source, drafts)

    def test_a_trailing_comment_at_end_of_file_closes_the_previous_declaration(self) -> None:
        source = b"def f():\n    return 1\n\n# end of module\n"
        drafts = chunks(source, "python")

        assert len(drafts) == 1
        assert drafts[0].symbol == "f"
        assert drafts[0].content.endswith("# end of module\n")
        assert_exact_coverage(source, drafts)


class TestAdjacentFragments:
    JSX = (
        b"const Navigation = () => {\n"
        b"  return 1;\n"
        b"};\n"
        b"\n"
        b"// --- PAGE 1: OVERVIEW ---\n"
        b"// --- PAGE 1: OVERVIEW ---\n"
        b"const Overview = () => {\n"
        b"  return 2;\n"
        b"};\n"
        b"\n"
        b"// --- ROUTER ---\n"
        b"function App() {\n"
        b"  return 3;\n"
        b"}\n"
    )

    def test_punctuation_and_dividers_between_components_leave_no_fragments(self) -> None:
        drafts = chunks(self.JSX, "jsx")

        assert [d.symbol for d in drafts] == ["Navigation", "Overview", "App"]
        assert not any(is_trivia_only(d.content, "jsx") for d in drafts)
        assert_exact_coverage(self.JSX, drafts)

    def test_closing_punctuation_stays_with_the_declaration_it_closes(self) -> None:
        found = by_symbol(chunks(self.JSX, "jsx"))

        assert found["Navigation"].content.endswith("};\n")
        assert "PAGE 1" not in found["Navigation"].content

    def test_each_divider_introduces_the_declaration_below_it(self) -> None:
        found = by_symbol(chunks(self.JSX, "jsx"))

        assert found["Overview"].content.startswith("// --- PAGE 1: OVERVIEW ---\n// --- PAGE 1")
        assert found["Overview"].start_line == 5
        assert found["App"].content.startswith("// --- ROUTER ---\nfunction App")
        assert found["App"].start_line == 11

    def test_adjacent_declarations_are_never_merged_together(self) -> None:
        found = by_symbol(chunks(self.JSX, "jsx"))

        assert "Overview" not in found["Navigation"].content
        assert "function App" not in found["Overview"].content


class TestLimitsAndScope:
    def test_a_merge_that_would_exceed_the_size_limit_is_not_made(self) -> None:
        function = b"def f():\n    return 1\n"
        source = b"# --- a divider comment that is quite long ---\n" + function
        limits = ChunkingLimits(max_chunk_chars=len(function) + 2, fallback_chunk_lines=120)

        drafts = chunks(source, "python", limits)
        found = by_symbol(drafts)

        assert found["f"].content.startswith("def f():")
        assert "divider" not in found["f"].content
        assert len(found["f"].content) <= limits.max_chunk_chars
        assert any("divider comment" in d.content for d in drafts if d.symbol is None)
        assert_exact_coverage(source, drafts)

    def test_a_comment_inside_an_oversized_class_joins_the_following_method(self) -> None:
        source = (
            b"class Service:\n"
            b"    def a(self):\n"
            b"        return 1\n\n"
            b"    # --- helpers ---\n"
            b"    def b(self):\n"
            b"        return 2\n"
        )
        limits = ChunkingLimits(max_chunk_chars=60, fallback_chunk_lines=120)

        drafts = chunks(source, "python", limits)
        method_b = next(d for d in drafts if d.symbol == "b")

        assert method_b.chunk_type == "method"
        assert method_b.parent_symbol == "Service"
        assert method_b.content.startswith("    # --- helpers ---\n    def b")
        assert "helpers" not in next(d for d in drafts if d.symbol == "a").content
        assert_exact_coverage(source, drafts)

    @pytest.mark.parametrize(
        "limits",
        [GENEROUS, ChunkingLimits(120, 120), ChunkingLimits(60, 5)],
    )
    def test_coverage_and_no_duplication_hold_on_a_realistic_file(
        self, limits: ChunkingLimits
    ) -> None:
        source = (
            b"from fastapi import FastAPI\nimport sqlite3\n\napp = FastAPI()\n\n"
            b"def get_db():\n    conn = sqlite3.connect('x.db')\n    return conn\n\n"
            b"# --- UPDATED DATA MODEL ---\n"
            b"class Order:\n    id: str\n\n"
            b"# --- 1. REAL-TIME INGESTION ---\n"
            b"@app.post('/ingest/order')\n"
            b"def ingest(order: Order):\n    conn = get_db()\n    return order.id\n\n\n"
            b"# --- 4. ANALYTICS TABS (EXISTING) ---\n\n"
            b"@app.get('/analytics/operations')\n"
            b"def get_operations_data():\n    return {}\n"
        )

        drafts = chunks(source, "python", limits)

        assert_exact_coverage(source, drafts)


class TestTriviaDetection:
    @pytest.mark.parametrize(
        ("text", "language", "expected"),
        [
            ("# --- PAGE 2 ---\n", "python", True),
            ("\n\n# a\n# b\n", "python", True),
            (";\n\n// --- ROUTER ---\n", "jsx", True),
            ("/**\n * Docs\n */\n", "typescript", True),
            ("import os\n", "python", False),
            ("export default App;\n", "jsx", False),
        ],
    )
    def test_recognises_comment_and_punctuation_only_text(
        self, text: str, language: str, expected: bool
    ) -> None:
        assert is_trivia_only(text, language) is expected
