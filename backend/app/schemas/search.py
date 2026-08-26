"""Schemas for semantic retrieval."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SearchRequest(BaseModel):
    """A retrieval query.

    ``top_k`` is bounded at both ends: a client cannot ask for zero results, nor
    for thousands of rows of source content.
    """

    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=8, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        """A whitespace-only query passes min_length but means nothing."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Query must contain at least one non-whitespace character.")
        return cleaned


class SearchResult(BaseModel):
    """One retrieved chunk, located precisely enough to open in an editor."""

    model_config = ConfigDict(from_attributes=True)

    chunk_id: uuid.UUID
    file_path: str
    language: str
    symbol: str | None
    parent_symbol: str | None
    chunk_type: str
    start_line: int
    end_line: int
    content: str
    # Cosine similarity in [-1, 1]; higher is nearer. A ranking signal, not a
    # probability or a relevance threshold.
    score: float


class SearchResponse(BaseModel):
    results: list[SearchResult]
    # Echoed so a caller can tell which model produced the ranking.
    model: str
    # How many chunks were searched, which makes an empty result set readable.
    searched_chunks: int
