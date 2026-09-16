"""Schemas for retrieval inspection."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, field_validator


class SearchRequest(BaseModel):
    """A retrieval query.

    ``top_k`` caps how many sources are selected (defaulting to
    CONTEXT_MAX_SOURCES). It is bounded at both ends: a client cannot ask for
    zero results, nor for thousands of rows of source content.
    """

    query: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        """A whitespace-only query passes min_length but means nothing."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Query must contain at least one non-whitespace character.")
        return cleaned


class CandidateResult(BaseModel):
    """One retrieval candidate, located precisely, with the signals that ranked it."""

    chunk_id: uuid.UUID
    file_path: str
    language: str
    symbol: str | None
    parent_symbol: str | None
    chunk_type: str
    start_line: int
    end_line: int
    # Cosine similarity in [-1, 1]; higher is nearer. A ranking signal, not a
    # probability or a relevance threshold.
    semantic_score: float
    semantic_rank: int | None
    lexical_rank: int | None
    # Share of the question's informative term weight present, in [0, 1].
    lexical_score: float
    matched_terms: list[str]
    # "symbol" | "path" | "identifier" when the question names this chunk.
    exact_match: str | None
    # Position in the fused ranking; null for a copy removed as a duplicate.
    final_rank: int | None
    # Comments/punctuation only; never used as evidence.
    low_value: bool


class SearchResult(CandidateResult):
    """A source selected into the answer context."""

    content: str
    # Same value as semantic_score; kept for clients of the earlier response.
    score: float


class SearchResponse(BaseModel):
    # Sources that would reach the model, in citation order.
    results: list[SearchResult]
    semantic_candidates: list[CandidateResult]
    lexical_candidates: list[CandidateResult]
    # "none" | "weak" | "useful" — a coarse label, not a confidence.
    strength: str
    # Echoed so a caller can tell which model produced the semantic ranking.
    model: str
    # How many chunks were searched, which makes an empty result set readable.
    searched_chunks: int
    query_terms: list[str]
    # False when the index predates lexical search and needs a re-index.
    lexical_available: bool
    context_chars: int
    context_budget: int
    max_sources: int
