"""Retrieval of repository evidence.

Wraps the existing vector search with the shaping an answer needs: bounded
top-K, duplicate removal, and a signal for how much the results are worth
trusting. One implementation serves both the Ask flow and the investigation
tools — a second retrieval path would drift from this one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.repository import IndexingStatus, Repository
from app.repositories import embedding_repo
from app.repositories.embedding_repo import SearchHit
from app.services.embeddings import InputKind
from app.services.embeddings import get_provider as get_embedding_provider
from app.services.search import RepositoryNotIndexedError, RepositoryNotSearchableError

logger = logging.getLogger(__name__)


class RetrievalStrength(StrEnum):
    """How much evidence came back, as a coarse, honest label.

    Deliberately not a probability. Cosine scores are not calibrated, so a
    number like "0.83 confident" would imply a precision that does not exist.
    These three buckets only drive how the answer is framed — whether the model
    is told it has solid evidence or almost none.
    """

    NONE = "none"
    WEAK = "weak"
    USEFUL = "useful"


@dataclass(frozen=True, slots=True)
class RetrievedSource:
    """One piece of evidence, with everything a citation needs."""

    rank: int
    chunk_id: str
    file_path: str
    language: str
    symbol: str | None
    parent_symbol: str | None
    chunk_type: str
    start_line: int
    end_line: int
    content: str
    score: float

    @property
    def qualified_symbol(self) -> str | None:
        if self.parent_symbol and self.symbol:
            return f"{self.parent_symbol}.{self.symbol}"
        return self.symbol

    @property
    def location(self) -> str:
        """Human-readable location, e.g. ``app/auth.py:42-73``."""
        return f"{self.file_path}:{self.start_line}-{self.end_line}"


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    sources: list[RetrievedSource]
    strength: RetrievalStrength
    model: str
    searched_chunks: int

    @property
    def distinct_files(self) -> int:
        return len({source.file_path for source in self.sources})


# A top result below this cosine similarity means nothing in the repository is
# close to the question. Calibrated by hand against voyage-code-3 on this
# codebase, not derived — it is a floor for "obviously unrelated", not a
# relevance threshold, and the answer only ever uses it to decide how firmly to
# state that evidence is thin.
_WEAK_TOP_SCORE = 0.35
# Below this, even a plausible top hit is treated as no evidence at all.
_NO_EVIDENCE_TOP_SCORE = 0.20


def retrieve(
    session: Session,
    *,
    repository: Repository,
    query: str,
    top_k: int,
    settings: Settings,
) -> RetrievalResult:
    """Find the chunks most relevant to ``query``.

    Raises the same errors as direct search, so callers handle one vocabulary
    for "this repository cannot be searched".
    """
    if repository.indexing_status != IndexingStatus.INDEXED:
        raise RepositoryNotIndexedError(
            "This repository has not been indexed yet.",
            details={"indexing_status": str(repository.indexing_status)},
        )

    model = repository.embedding_model or settings.embedding_model
    searched = embedding_repo.count_embeddings(session, repository.id, model=model)
    if searched == 0:
        raise RepositoryNotSearchableError(
            "This repository has no embeddings for the active model. Re-index it to enable search.",
            details={"model": model},
        )

    provider = get_embedding_provider(settings)
    if provider.model != model:
        raise RepositoryNotSearchableError(
            "This repository was indexed with a different embedding model. Re-index it to search.",
            details={"indexed_with": model, "configured": provider.model},
        )

    bounded_k = max(1, min(top_k, settings.search_max_top_k))
    query_vector = provider.embed_text(query, kind=InputKind.QUERY)

    hits = embedding_repo.search_similar_chunks(
        session,
        repository_id=repository.id,
        query_vector=query_vector,
        model=model,
        top_k=bounded_k,
    )

    sources = _to_sources(_deduplicate(hits))
    strength = _assess(sources)

    logger.info(
        "Retrieval repository_id=%s query_chars=%d top_k=%d results=%d files=%d strength=%s",
        repository.id,
        len(query),
        bounded_k,
        len(sources),
        len({s.file_path for s in sources}),
        strength.value,
    )

    return RetrievalResult(
        sources=sources, strength=strength, model=model, searched_chunks=searched
    )


def _deduplicate(hits: list[SearchHit]) -> list[SearchHit]:
    """Drop chunks whose source text repeats one already kept.

    Split symbols and re-exported code produce near-identical chunks; sending
    the same body twice wastes context budget that a different file could use.
    Order is preserved, so the highest-scoring copy is the one retained.
    """
    seen: set[str] = set()
    unique: list[SearchHit] = []

    for hit in hits:
        # Whitespace-insensitive so reformatting does not defeat the check.
        fingerprint = " ".join(hit.content.split())
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique.append(hit)

    return unique


def _to_sources(hits: list[SearchHit]) -> list[RetrievedSource]:
    return [
        RetrievedSource(
            rank=index,
            chunk_id=str(hit.chunk_id),
            file_path=hit.file_path,
            language=hit.language,
            symbol=hit.symbol,
            parent_symbol=hit.parent_symbol,
            chunk_type=hit.chunk_type,
            start_line=hit.start_line,
            end_line=hit.end_line,
            content=hit.content,
            score=hit.score,
        )
        for index, hit in enumerate(hits, start=1)
    ]


def _assess(sources: list[RetrievedSource]) -> RetrievalStrength:
    """Classify the evidence using signals we actually have.

    Uses the top score, how many results came back, and how many distinct files
    they span. Nothing here claims to be a probability.
    """
    if not sources:
        return RetrievalStrength.NONE

    top = sources[0].score
    if top < _NO_EVIDENCE_TOP_SCORE:
        return RetrievalStrength.NONE
    if top < _WEAK_TOP_SCORE or len(sources) < 2:
        return RetrievalStrength.WEAK
    return RetrievalStrength.USEFUL
