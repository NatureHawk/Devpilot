"""Full-text queries over ``code_chunks.search_vector``.

The vector is a generated column built from ``lexical_names`` (weight A) and
``lexical_body`` (weight D), both written at indexing time by
:func:`app.services.lexical.chunk_lexical_fields`. Raw SQL is used because the
queries lean on PostgreSQL text-search functions that have no ORM spelling
worth the indirection.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.lexical import TermStatistics


@dataclass(frozen=True, slots=True)
class LexicalHit:
    """One chunk matching a text-search query."""

    chunk_id: uuid.UUID
    file_path: str
    language: str
    symbol: str | None
    parent_symbol: str | None
    chunk_type: str
    start_line: int
    end_line: int
    content: str
    # ts_rank_cd with length normalisation. Only comparable within one query.
    text_rank: float
    # Every normalised term stored for the chunk, for matched-term accounting.
    terms: frozenset[str]


def term_statistics(
    session: Session, repository_id: uuid.UUID, terms: tuple[str, ...]
) -> tuple[TermStatistics, bool]:
    """Document frequency of each term, and whether lexical data exists at all.

    The second value is False for an index built before lexical fields were
    stored: every chunk has an empty body, and a re-index is needed. That is
    reported rather than silently returning no matches.
    """
    counts = session.execute(
        text(
            "SELECT count(*) AS total, count(*) FILTER (WHERE lexical_body <> '') AS with_terms "
            "FROM code_chunks WHERE repository_id = :repository_id"
        ),
        {"repository_id": repository_id},
    ).one()

    frequencies: dict[str, int] = {}
    if terms and counts.with_terms:
        rows = session.execute(
            text(
                "SELECT t.term, count(c.id) AS df "
                "FROM unnest(CAST(:terms AS text[])) AS t(term) "
                "LEFT JOIN code_chunks c ON c.repository_id = :repository_id "
                "AND c.search_vector @@ to_tsquery('simple', t.term) "
                "GROUP BY t.term"
            ),
            {"terms": list(terms), "repository_id": repository_id},
        )
        frequencies = {row.term: int(row.df) for row in rows}

    return (
        TermStatistics(total_chunks=int(counts.total), document_frequency=frequencies),
        bool(counts.with_terms),
    )


def search_chunks(
    session: Session, *, repository_id: uuid.UUID, tsquery: str, limit: int
) -> list[LexicalHit]:
    """Chunks matching ``tsquery``, best text rank first.

    ``ts_rank_cd`` normalisation 1 divides by ``1 + log(length)`` so a long chunk
    does not win merely by containing more words.
    """
    if not tsquery or limit <= 0:
        return []

    rows = session.execute(
        text(
            "SELECT c.id, f.path, c.language, c.symbol, c.parent_symbol, "
            "CAST(c.chunk_type AS text) AS chunk_type, c.start_line, c.end_line, c.content, "
            "c.lexical_names, c.lexical_body, "
            "ts_rank_cd(c.search_vector, q.query, 1) AS text_rank "
            "FROM code_chunks c "
            "JOIN files f ON f.id = c.file_id "
            "CROSS JOIN to_tsquery('simple', :tsquery) AS q(query) "
            "WHERE c.repository_id = :repository_id AND c.search_vector @@ q.query "
            "ORDER BY text_rank DESC, c.id "
            "LIMIT :limit"
        ),
        {"tsquery": tsquery, "repository_id": repository_id, "limit": limit},
    )

    return [
        LexicalHit(
            chunk_id=row.id,
            file_path=row.path,
            language=row.language,
            symbol=row.symbol,
            parent_symbol=row.parent_symbol,
            chunk_type=row.chunk_type,
            start_line=row.start_line,
            end_line=row.end_line,
            content=row.content,
            text_rank=float(row.text_rank),
            terms=frozenset(f"{row.lexical_names} {row.lexical_body}".split()),
        )
        for row in rows
    ]
