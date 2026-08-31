"""Retrieval deduplication: near-identical chunks must not each take a slot.

Pure unit test of ``app.services.retrieval._deduplicate`` — no database, no
provider. Split symbols and re-exported code produce chunks whose body repeats;
sending the same text twice wastes context budget a different file could use.
"""

from __future__ import annotations

from app.repositories.embedding_repo import SearchHit
from app.services.retrieval import _deduplicate


def _hit(chunk_id: str, content: str, score: float) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        file_path=f"{chunk_id}.py",
        language="python",
        symbol="f",
        parent_symbol=None,
        chunk_type="function",
        start_line=1,
        end_line=9,
        content=content,
        score=score,
    )


def test_identical_bodies_collapse_to_the_first() -> None:
    hits = [
        _hit("a", "def f():\n    return 1", 0.90),
        _hit("b", "def f():\n    return 1", 0.80),
        _hit("c", "def g():\n    return 2", 0.70),
    ]

    kept = _deduplicate(hits)

    assert [h.chunk_id for h in kept] == ["a", "c"]


def test_dedup_is_whitespace_insensitive() -> None:
    """Reformatting must not defeat the check."""
    hits = [
        _hit("a", "def f():\n    return 1", 0.9),
        _hit("b", "def f():\n        return   1", 0.8),
    ]

    assert [h.chunk_id for h in _deduplicate(hits)] == ["a"]


def test_highest_scoring_copy_is_the_one_retained() -> None:
    hits = [
        _hit("low", "same body", 0.40),
        _hit("high", "same body", 0.95),
    ]
    # Retrieval hands hits in score order, so the first occurrence is the best.
    ordered = sorted(hits, key=lambda h: h.score, reverse=True)

    kept = _deduplicate(ordered)

    assert [h.chunk_id for h in kept] == ["high"]


def test_distinct_bodies_are_all_kept_in_order() -> None:
    hits = [_hit(str(i), f"body {i}", 0.9 - i / 100) for i in range(5)]

    assert [h.chunk_id for h in _deduplicate(hits)] == ["0", "1", "2", "3", "4"]


def test_empty_input_returns_empty() -> None:
    assert _deduplicate([]) == []
