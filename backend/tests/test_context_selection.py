"""Budget-aware context selection. Pure; no database, no provider."""

from __future__ import annotations

import pytest

from app.services.context_builder import build_context, select_sources
from app.services.retrieval import RetrievalResult, RetrievalStrength, RetrievedSource

OVERHEAD = 200


def source(
    rank: int,
    *,
    path: str | None = None,
    chars: int = 400,
    semantic_rank: int | None = None,
    semantic_excess: float | None = 1.0,
    lexical_score: float = 0.0,
    exact_match: str | None = None,
    low_value: bool = False,
    start_line: int | None = None,
    parent_symbol: str | None = None,
) -> RetrievedSource:
    return RetrievedSource(
        rank=rank,
        chunk_id=f"chunk-{rank}",
        file_path=path or f"app/f{rank}.py",
        language="python",
        symbol=f"symbol_{rank}",
        parent_symbol=parent_symbol,
        chunk_type="function",
        start_line=start_line if start_line is not None else rank * 10,
        end_line=(start_line if start_line is not None else rank * 10) + 5,
        content="x" * (chars - 1) + "\n",
        score=0.7,
        semantic_rank=semantic_rank if semantic_rank is not None else rank,
        semantic_excess=semantic_excess,
        lexical_rank=1 if lexical_score else None,
        lexical_score=lexical_score,
        exact_match=exact_match,
        low_value=low_value,
    )


def retrieval(sources: list[RetrievedSource], strength=RetrievalStrength.USEFUL) -> RetrievalResult:
    return RetrievalResult(
        sources=sources, strength=strength, model="m", searched_chunks=len(sources)
    )


class TestBudget:
    def test_fills_the_budget_past_the_old_fixed_eight(self) -> None:
        candidates = [source(rank, chars=500) for rank in range(1, 16)]

        included, _, _ = select_sources(candidates, max_chars=20_000, max_sources=12)

        assert len(included) == 12

    def test_never_exceeds_the_character_budget(self) -> None:
        candidates = [source(rank, chars=700 + rank * 37) for rank in range(1, 30)]

        included, _, _ = select_sources(candidates, max_chars=6_000, max_sources=30)

        assert sum(len(s.content) + OVERHEAD for s in included) <= 6_000

    def test_skips_a_chunk_that_does_not_fit_rather_than_splitting_it(self) -> None:
        candidates = [
            source(1, chars=1_000),
            source(2, chars=3_000),
            source(3, chars=500),
        ]

        included, dropped, truncated = select_sources(candidates, max_chars=2_000)

        assert [s.rank for s in included] == [1, 3]
        assert [s.rank for s in dropped] == [2]
        assert truncated == 0
        assert all("excerpt truncated" not in s.content for s in included)

    def test_truncates_only_a_top_source_larger_than_the_whole_budget(self) -> None:
        included, _, truncated = select_sources(
            [source(1, chars=10_000), source(2, chars=300)], max_chars=3_000
        )

        assert truncated == 1
        assert included[0].rank == 1 and "excerpt truncated" in included[0].content

    def test_stops_at_the_configured_source_cap(self) -> None:
        included, dropped, _ = select_sources(
            [source(rank, chars=100) for rank in range(1, 10)], max_chars=50_000, max_sources=4
        )

        assert len(included) == 4
        assert len(dropped) == 5


class TestUsefulness:
    def test_a_vector_no_nearer_than_chance_is_not_evidence(self) -> None:
        candidates = [source(1), source(2, semantic_rank=30, semantic_excess=0.1)]

        included, dropped, _ = select_sources(candidates, max_chars=50_000, max_sources=12)

        assert [s.rank for s in included] == [1]
        assert [s.rank for s in dropped] == [2]

    @pytest.mark.parametrize("overrides", [{"lexical_score": 0.6}, {"exact_match": "identifier"}])
    def test_a_strong_lexical_or_exact_match_is_evidence_at_any_semantic_rank(
        self, overrides
    ) -> None:
        candidate = source(1, semantic_rank=35, semantic_excess=None, **overrides)

        included, _, _ = select_sources([candidate], max_chars=50_000, max_sources=12)

        assert included == [candidate]

    def test_low_value_chunks_are_dropped_when_real_evidence_exists(self) -> None:
        candidates = [source(1, low_value=True), source(2)]

        included, dropped, _ = select_sources(candidates, max_chars=50_000)

        assert [s.rank for s in included] == [2]
        assert dropped[0].low_value

    def test_no_evidence_limits_context_to_a_few_nearest_sources(self) -> None:
        candidates = [source(rank, chars=200) for rank in range(1, 13)]

        context = build_context(
            repository_full_name="acme/api",
            retrieval=retrieval(candidates, strength=RetrievalStrength.NONE),
            max_chars=50_000,
            max_sources=12,
        )

        assert len(context.included) == 3

    def test_one_incidental_word_in_common_is_not_evidence(self) -> None:
        candidate = source(1, semantic_rank=20, semantic_excess=-0.3, lexical_score=0.07)

        included, dropped, _ = select_sources([candidate], max_chars=50_000, max_sources=12)

        assert included == []
        assert dropped == [candidate]

    def test_no_evidence_offers_the_nearest_code_by_meaning_not_fused_order(self) -> None:
        """Fused order there is driven by keyword hits nothing corroborates."""
        candidates = [
            source(rank, semantic_rank=13 - rank, lexical_score=0.07, semantic_excess=-0.5)
            for rank in range(1, 13)
        ]

        context = build_context(
            repository_full_name="acme/api",
            retrieval=retrieval(candidates, strength=RetrievalStrength.NONE),
            max_chars=50_000,
            max_sources=12,
        )

        assert [s.semantic_rank for s in context.included] == [1, 2, 3]


class TestDiversity:
    def test_one_file_cannot_crowd_out_the_next_file(self) -> None:
        candidates = [source(rank, path="app/big.py") for rank in range(1, 6)]
        candidates.append(source(6, path="app/other.py", lexical_score=0.6))

        included, _, _ = select_sources(candidates, max_chars=50_000, max_sources=4)

        assert [s.file_path for s in included].count("app/big.py") == 3
        assert included[-1].file_path == "app/other.py"

    def test_deferred_chunks_still_fill_remaining_room(self) -> None:
        candidates = [source(rank, path="app/big.py") for rank in range(1, 6)]

        included, _, _ = select_sources(candidates, max_chars=50_000, max_sources=12)

        assert len(included) == 5


class TestRendering:
    def test_metadata_is_preserved_and_a_file_reads_in_line_order(self) -> None:
        later = source(1, path="app/api.py", start_line=90, parent_symbol="Service")
        earlier = source(2, path="app/api.py", start_line=12)

        context = build_context(
            repository_full_name="acme/api",
            retrieval=retrieval([later, earlier]),
            max_chars=50_000,
        )

        assert context.text.index("[S2] app/api.py:12-17") < context.text.index(
            "[S1] app/api.py:90-95"
        )
        assert "Service.symbol_1" in context.text
        assert context.citation_labels == {"S1": "chunk-1", "S2": "chunk-2"}

    def test_records_how_much_of_the_budget_was_used(self) -> None:
        context = build_context(
            repository_full_name="acme/api",
            retrieval=retrieval([source(1, chars=300), source(2, chars=500)]),
            max_chars=10_000,
        )

        assert context.source_chars == 800
        assert context.budget == 10_000
