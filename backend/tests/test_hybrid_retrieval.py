"""Hybrid ranking and retrieval-strength classification. Pure; no database, no provider.

``rank_candidates`` receives what the repositories would return; the tests
build those inputs directly so each ranking rule can be observed in isolation.
"""

from __future__ import annotations

import uuid

import pytest

from app.repositories.embedding_repo import SearchHit
from app.repositories.lexical_repo import LexicalHit
from app.services.lexical import TermStatistics, query_terms, text_terms
from app.services.retrieval import (
    RetrievalSignals,
    RetrievalStrength,
    assess_strength,
    rank_candidates,
    semantic_excess,
    semantic_separation,
)

_IDS: dict[str, uuid.UUID] = {}


def cid(name: str) -> uuid.UUID:
    return _IDS.setdefault(name, uuid.uuid5(uuid.NAMESPACE_OID, name))


def sem(
    name: str,
    score: float,
    *,
    path: str = "app/mod.py",
    content: str | None = None,
    symbol: str | None = None,
    language: str = "python",
) -> SearchHit:
    return SearchHit(
        chunk_id=cid(name),
        file_path=path,
        language=language,
        symbol=symbol if symbol is not None else name,
        parent_symbol=None,
        chunk_type="function",
        start_line=1,
        end_line=5,
        content=content if content is not None else f"def {name}():\n    return compute_{name}()",
        score=score,
    )


def lex(
    name: str,
    content: str,
    *,
    path: str = "app/mod.py",
    symbol: str | None = None,
    text_rank: float = 0.1,
    language: str = "python",
) -> LexicalHit:
    return LexicalHit(
        chunk_id=cid(name),
        file_path=path,
        language=language,
        symbol=symbol,
        parent_symbol=None,
        chunk_type="function",
        start_line=1,
        end_line=5,
        content=content,
        text_rank=text_rank,
        terms=frozenset(text_terms(f"{path} {symbol or ''} {content}")),
    )


def flat_pool(count: int = 20, *, top: float = 0.70, step: float = 0.002) -> list[SearchHit]:
    """Evenly spaced scores: no candidate stands out from the rest."""
    return [sem(f"generic_{i}", top - i * step, path=f"app/generic_{i}.py") for i in range(count)]


class TestSemanticRetrieval:
    def test_without_lexical_matches_the_semantic_order_is_kept(self) -> None:
        hits = flat_pool(10)

        ranked = rank_candidates(
            terms=query_terms("how are totals computed"),
            stats=TermStatistics(total_chunks=10),
            semantic_hits=hits,
            lexical_hits=[],
        )

        assert [s.chunk_id for s in ranked.sources] == [str(h.chunk_id) for h in hits]
        assert [s.semantic_rank for s in ranked.sources] == list(range(1, 11))

    def test_semantic_score_is_preserved_unmodified(self) -> None:
        ranked = rank_candidates(
            terms=query_terms("totals"),
            stats=TermStatistics(total_chunks=1),
            semantic_hits=[sem("a", 0.6612)],
            lexical_hits=[],
        )

        assert ranked.sources[0].score == 0.6612


class TestLexicalRetrieval:
    def test_an_exact_identifier_outside_the_semantic_pool_is_found_and_ranked_first(self) -> None:
        operations = lex(
            "operations",
            "const categories = useMemo(() => {\n  return keys;\n}, [data]);",
            path="web/App.jsx",
            symbol="Operations",
            language="jsx",
        )

        ranked = rank_candidates(
            terms=query_terms("Where is useMemo used?"),
            stats=TermStatistics(
                total_chunks=50, document_frequency={"usememo": 1, "use": 6, "memo": 1}
            ),
            semantic_hits=flat_pool(20),
            lexical_hits=[operations],
            extra_semantic_scores={cid("operations"): 0.41},
        )

        top = ranked.sources[0]
        assert top.chunk_id == str(cid("operations"))
        assert top.exact_match == "identifier"
        assert top.lexical_rank == 1
        assert top.semantic_rank is None
        assert top.score == 0.41  # its real cosine score, fetched separately
        assert ranked.lexical[0].chunk_id == top.chunk_id

    def test_a_symbol_named_in_the_question_outranks_a_mere_mention(self) -> None:
        definition = lex(
            "get_db", "def get_db():\n    return sqlite3.connect(DB_PATH)", symbol="get_db"
        )
        caller = lex(
            "caller", "def report():\n    conn = get_db()\n    return conn", symbol="report"
        )

        ranked = rank_candidates(
            terms=query_terms("Where is get_db defined?"),
            stats=TermStatistics(
                total_chunks=40, document_frequency={"getdb": 2, "get": 4, "db": 5}
            ),
            semantic_hits=[],
            lexical_hits=[caller, definition],
        )

        assert ranked.sources[0].chunk_id == str(cid("get_db"))
        assert ranked.sources[0].exact_match == "symbol"
        assert ranked.sources[1].exact_match == "identifier"


class TestHybridRanking:
    def test_agreement_between_lists_beats_topping_only_one(self) -> None:
        semantic = [sem("nearest", 0.80), sem("other", 0.79), sem("both", 0.78)]
        lexical = [lex("both", "def both():\n    return sqlite3.connect(DB_PATH)", symbol="both")]

        ranked = rank_candidates(
            terms=query_terms("sqlite connection"),
            stats=TermStatistics(
                total_chunks=30, document_frequency={"sqlite": 2, "connection": 0}
            ),
            semantic_hits=semantic,
            lexical_hits=lexical,
        )

        assert ranked.sources[0].chunk_id == str(cid("both"))
        assert ranked.sources[0].fused_score > ranked.sources[1].fused_score

    def test_fused_score_is_not_presented_as_a_probability(self) -> None:
        ranked = rank_candidates(
            terms=query_terms("x"),
            stats=TermStatistics(total_chunks=3),
            semantic_hits=flat_pool(3),
            lexical_hits=[],
        )

        assert all(0 < s.fused_score < 0.1 for s in ranked.sources)

    def test_comment_only_chunks_never_take_a_rank_from_real_code(self) -> None:
        divider = sem("divider", 0.90, content="# --- 2. DEMO SIMULATION ---\n", symbol="")
        real = sem("simulate_sale", 0.70)

        ranked = rank_candidates(
            terms=query_terms("simulate a sale"),
            stats=TermStatistics(total_chunks=2),
            semantic_hits=[divider, real],
            lexical_hits=[],
        )

        assert ranked.sources[0].chunk_id == str(cid("simulate_sale"))
        assert ranked.sources[-1].low_value is True
        assert ranked.sources[-1].fused_score == 0
        assert ranked.signals.evidence_candidates == 1


class TestDuplicateRemoval:
    def test_identical_bodies_keep_only_the_better_ranked_copy(self) -> None:
        body = "def f():\n    return 1"
        ranked = rank_candidates(
            terms=query_terms("f"),
            stats=TermStatistics(total_chunks=2),
            semantic_hits=[sem("a", 0.9, content=body), sem("b", 0.8, content=body, path="b.py")],
            lexical_hits=[],
        )

        assert [s.chunk_id for s in ranked.sources] == [str(cid("a"))]
        # The copy remains visible in the per-list view, without a final rank.
        assert [s.rank for s in ranked.semantic] == [1, 0]

    def test_near_identical_bodies_collapse(self) -> None:
        body = (
            "def load_orders(conn, period, city):\n"
            "    rows = conn.execute(query, (period, city)).fetchall()\n"
            "    return [dict(row) for row in rows]\n"
        )
        # One extra token out of fifteen: Jaccard 14/15 ≈ 0.93.
        near = body + "# copied\n"

        ranked = rank_candidates(
            terms=query_terms("orders"),
            stats=TermStatistics(total_chunks=2),
            semantic_hits=[
                sem("orig", 0.9, content=body),
                sem("copy", 0.85, content=near, path="c.py"),
            ],
            lexical_hits=[],
        )

        assert [s.chunk_id for s in ranked.sources] == [str(cid("orig"))]

    def test_short_similar_helpers_are_not_collapsed(self) -> None:
        ranked = rank_candidates(
            terms=query_terms("helpers"),
            stats=TermStatistics(total_chunks=2),
            semantic_hits=[
                sem("a", 0.9, content="def a():\n    return 1"),
                sem("b", 0.8, content="def b():\n    return 2"),
            ],
            lexical_hits=[],
        )

        assert len(ranked.sources) == 2


def signals(**overrides: object) -> RetrievalSignals:
    values: dict[str, object] = {
        "evidence_candidates": 20,
        "exact_matches": 0,
        "strong_lexical": 0,
        "partial_lexical": 0,
        "corroborated": 0,
        "agreeing_files": 0,
        "semantic_separation": None,
        "semantic_excess": 0.0,
    }
    values.update(overrides)
    return RetrievalSignals(**values)  # type: ignore[arg-type]


class TestStrengthClassification:
    def test_strong_semantic_outlier_is_useful(self) -> None:
        assert assess_strength(signals(semantic_excess=0.9)) is RetrievalStrength.USEFUL

    def test_thin_semantic_outlier_alone_is_weak(self) -> None:
        assert assess_strength(signals(semantic_excess=0.3)) is RetrievalStrength.WEAK

    def test_exact_lexical_match_is_useful_even_when_semantics_are_flat(self) -> None:
        result = assess_strength(signals(exact_matches=1, partial_lexical=1, semantic_excess=-0.2))
        assert result is RetrievalStrength.USEFUL

    def test_no_meaningful_match_is_none(self) -> None:
        assert assess_strength(signals(semantic_excess=0.05)) is RetrievalStrength.NONE

    def test_a_best_match_no_better_than_chance_is_none(self) -> None:
        """A top score ~2 standard deviations above the pool mean is what 40 unrelated chunks
        produce anyway."""
        result = assess_strength(signals(semantic_separation=2.2, semantic_excess=0.04))
        assert result is RetrievalStrength.NONE

    def test_incidental_single_word_matches_are_not_evidence(self) -> None:
        """"images shown in order" matching an Order class is how noise looks relevant."""
        assert assess_strength(signals(partial_lexical=12)) is RetrievalStrength.NONE

    def test_uncorroborated_strong_lexical_match_is_weak(self) -> None:
        result = assess_strength(signals(strong_lexical=1, partial_lexical=1))
        assert result is RetrievalStrength.WEAK

    def test_agreement_across_multiple_relevant_files_is_useful(self) -> None:
        result = assess_strength(
            signals(strong_lexical=3, partial_lexical=3, corroborated=2, agreeing_files=2)
        )
        assert result is RetrievalStrength.USEFUL

    def test_only_low_value_candidates_is_none(self) -> None:
        assert assess_strength(signals(evidence_candidates=0, semantic_excess=5.0)) is (
            RetrievalStrength.NONE
        )

    def test_many_low_value_candidates_end_to_end(self) -> None:
        dividers = [
            sem(f"div_{i}", 0.9 - i / 100, content=f"# --- SECTION {i} ---\n", symbol="")
            for i in range(12)
        ]

        ranked = rank_candidates(
            terms=query_terms("where are sections"),
            stats=TermStatistics(total_chunks=12),
            semantic_hits=dividers,
            lexical_hits=[],
        )

        assert all(s.low_value for s in ranked.sources)
        assert ranked.strength is RetrievalStrength.NONE


class TestUnsupportedQuestions:
    def test_absent_vocabulary_and_a_flat_semantic_pool_is_none(self) -> None:
        ranked = rank_candidates(
            terms=query_terms("Where is Stripe webhook processing implemented?"),
            stats=TermStatistics(total_chunks=48, document_frequency={}),
            semantic_hits=flat_pool(40),
            lexical_hits=[],
        )

        assert ranked.signals.partial_lexical == 0
        assert ranked.strength is RetrievalStrength.NONE

    def test_a_clear_semantic_outlier_is_not_mistaken_for_no_evidence(self) -> None:
        hits = [sem("answer", 0.86), *flat_pool(30, top=0.66)]

        ranked = rank_candidates(
            terms=query_terms("how is the connection opened"),
            stats=TermStatistics(total_chunks=31),
            semantic_hits=hits,
            lexical_hits=[],
        )

        assert ranked.strength is RetrievalStrength.USEFUL


class TestSemanticSeparation:
    def test_is_unmeasurable_for_a_tiny_pool(self) -> None:
        assert semantic_separation([0.9, 0.5, 0.4]) is None

    def test_is_unmeasurable_when_every_other_score_is_identical(self) -> None:
        assert semantic_separation([0.9] + [0.5] * 10) is None

    def test_is_scale_free(self) -> None:
        """The same shape on a compressed cosine range gives the same separation."""
        wide = [0.9, 0.5, 0.48, 0.46, 0.44, 0.42, 0.40, 0.38, 0.36]
        narrow = [0.70 + (s - 0.36) / 10 for s in wide]

        assert semantic_separation(wide) == pytest.approx(semantic_separation(narrow))


class TestSemanticExcess:
    def test_an_evenly_spread_pool_has_no_excess_over_chance(self) -> None:
        """The best of evenly spaced scores is exactly what rank alone predicts, or less."""
        scores = [0.70 - i * 0.002 for i in range(40)]

        excess = semantic_excess(scores)

        assert excess[0] is not None and excess[0] < 0.25

    def test_a_genuine_outlier_stands_well_above_chance(self) -> None:
        scores = [0.86] + [0.66 - i * 0.002 for i in range(30)]

        excess = semantic_excess(scores)

        assert excess[0] is not None and excess[0] > 0.5
        assert all(value is not None and value < 0.5 for value in excess[1:])

    def test_is_scale_free(self) -> None:
        wide = [0.9, 0.6, 0.55, 0.5, 0.48, 0.46, 0.44, 0.42, 0.40, 0.38]
        narrow = [0.70 + (s - 0.38) / 10 for s in wide]

        assert semantic_excess(wide) == pytest.approx(semantic_excess(narrow))

    def test_is_unmeasurable_for_a_tiny_or_degenerate_pool(self) -> None:
        assert semantic_excess([0.9, 0.5]) == [None, None]
        assert semantic_excess([0.9] + [0.5] * 10) == [None] * 11
