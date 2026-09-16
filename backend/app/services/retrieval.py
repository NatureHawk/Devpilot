"""Retrieval of repository evidence: semantic and lexical, fused.

One implementation serves the Ask flow, the retrieval inspector and the
investigation tools — a second retrieval path would drift from this one.

    question ─┬─ embed ─► pgvector cosine, top N ─────────────┐ semantic list
              └─ terms ─► PostgreSQL full-text, top M ────────┤ lexical list
                                                              ├─► exact-match list
                                                              ▼
                         reciprocal rank fusion ─► de-duplicate ─► ranked candidates
                                                                   + strength (none/weak/useful)

Choosing which candidates fit an answer's budget is the context builder's job.

Ranking
-------
The lists are combined with Reciprocal Rank Fusion (Cormack, Clarke & Büttcher,
SIGIR 2009)::

    fused(chunk) = Σ over lists  1 / (k + rank_in_list),   k = 60

RRF uses ranks, never raw scores, and that is the point: cosine similarity and
``ts_rank_cd`` live on unrelated, uncalibrated scales — and one embedding
model's cosine range is not another's — so adding or weighting the raw numbers
would bake one model's score distribution into the ranking. A chunk near the
top of several lists beats a chunk at the top of only one. The fused value
orders candidates; it is not a probability and is exposed only for debugging.

- semantic: cosine similarity to the embedded question.
- lexical: the idf-weighted share of the question's informative terms the chunk
  contains (:meth:`TermStatistics.coverage`), ties broken by ``ts_rank_cd``.
- named: chunks whose symbol or file the question names exactly (``get_db``,
  ``App.jsx``); symbol matches first, then path.
- mentioned: chunks containing an identifier-shaped question token verbatim
  (``useMemo``, ``/analytics/operations``).

Named and mentioned are separate lists on purpose: a definition of ``get_db``
both names and mentions it, a caller only mentions it, so the definition
outranks its callers structurally instead of by a tie-break.

Chunk quality: a chunk of only comments and punctuation (possible in an index
built before gap trivia was attached to declarations) stays visible but is
marked low-value. It takes no rank in any fused list, never counts as evidence,
and is not selected into context.
"""

from __future__ import annotations

import logging
import math
import re
import statistics
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, TypeVar

from fastapi import status
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.models.repository import IndexingStatus, Repository
from app.repositories import embedding_repo, lexical_repo
from app.repositories.embedding_repo import SearchHit
from app.repositories.lexical_repo import LexicalHit
from app.services import lexical
from app.services.embeddings import InputKind
from app.services.embeddings import get_provider as get_embedding_provider
from app.services.indexing.chunker import is_trivia_only

logger = logging.getLogger(__name__)


class RepositoryNotIndexedError(AppError):
    """Search was attempted before the repository had a usable index."""

    status_code = status.HTTP_409_CONFLICT
    code = "repository_not_indexed"


class RepositoryNotSearchableError(AppError):
    """The repository is indexed but holds no vectors for the active model.

    Distinct from an empty result set on purpose: no matches is an answer, while
    no vectors means the question was never actually asked of anything.
    """

    status_code = status.HTTP_409_CONFLICT
    code = "repository_not_searchable"


class RetrievalStrength(StrEnum):
    """How much evidence came back, as a coarse, honest label.

    Deliberately not a probability. Similarity scores are not calibrated, so a
    number like "0.83 confident" would imply a precision that does not exist.
    These three buckets only drive how the answer is framed — whether the model
    is told it has solid evidence, thin evidence, or none.
    """

    NONE = "none"
    WEAK = "weak"
    USEFUL = "useful"


@dataclass(frozen=True, slots=True)
class RetrievedSource:
    """One candidate piece of evidence, with everything a citation needs."""

    # Position in the fused ranking, 1-based. 0 for a copy removed as a
    # duplicate of a better-ranked chunk (it appears only in the per-list views).
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
    # Cosine similarity to the question — the semantic score, unmodified.
    score: float
    # Position in the semantic candidate pool; None if not in it.
    semantic_rank: int | None = None
    # Standard deviations above chance for its semantic rank (see
    # SEMANTIC_EVIDENCE_EXCESS). None when outside the pool or unmeasurable.
    semantic_excess: float | None = None
    # Position in the lexical ranking; None if no informative term matched.
    lexical_rank: int | None = None
    # Idf-weighted share of the question's informative terms present, in [0, 1].
    lexical_score: float = 0.0
    matched_terms: tuple[str, ...] = ()
    # "symbol" | "path" | "identifier" when the question names this chunk exactly.
    exact_match: str | None = None
    # Reciprocal-rank-fusion sum. Orders candidates; not a probability.
    fused_score: float = 0.0
    # Comments/punctuation only: shown, never used as evidence.
    low_value: bool = False

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
class RetrievalSignals:
    """The observations strength is classified from. All counts exclude low-value chunks."""

    evidence_candidates: int
    # Chunks the question names exactly (symbol, file, or verbatim identifier).
    exact_matches: int
    # Chunks matching at least half the question's informative term weight.
    strong_lexical: int
    # Chunks matching any informative question term.
    partial_lexical: int
    # Strong lexical matches that are also near the top of the semantic list:
    # two independent methods agreeing on the same chunk.
    corroborated: int
    # Distinct files among exact and corroborated matches.
    agreeing_files: int
    # How far the best semantic score stands above the rest of the semantic
    # pool, in standard deviations of that pool. Relative to this query's own
    # score distribution, so it means the same for any embedding model. None
    # when the pool is too small to measure a spread.
    semantic_separation: float | None
    # The best semantic match's excess over chance (see SEMANTIC_EVIDENCE_EXCESS).
    semantic_excess: float | None


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    # Fused, de-duplicated candidates, best first.
    sources: list[RetrievedSource]
    strength: RetrievalStrength
    model: str
    searched_chunks: int
    # Per-list views for inspection, each in its own rank order.
    semantic: list[RetrievedSource] = field(default_factory=list)
    lexical: list[RetrievedSource] = field(default_factory=list)
    query_terms: tuple[str, ...] = ()
    # False when the index predates lexical fields and needs a re-index.
    lexical_available: bool = True
    signals: RetrievalSignals | None = None

    @property
    def distinct_files(self) -> int:
        return len({source.file_path for source in self.sources})


# Reciprocal Rank Fusion constant, the value published with the method. It damps
# the gap between adjacent top ranks so no single list decides alone.
_RRF_K = 60

# A chunk is "strongly" lexically matched when it holds at least half the
# question's informative (idf) term weight — more of the question than not.
STRONG_LEXICAL_COVERAGE = 0.5

# Semantic ranks at which a strong lexical match counts as corroborated. A rank
# cut, not a score cut: it holds for any embedding model's score scale.
_AGREEMENT_DEPTH = 10

# Semantic evidence is measured as *excess over chance*: how far a candidate's
# score stands above where the k-th best of n unrelated chunks would be expected
# to fall, in standard deviations of this query's own semantic pool. Even
# unrelated chunks produce a "best" score well above the pool mean — for 40
# chunks, about two standard deviations — so a raw separation cut-off would
# mistake that for evidence. The expectation uses Blom's approximation to normal
# order statistics, inv_normal_cdf((n - k + 0.625) / (n + 0.25)). Relative to the query's
# own score distribution, it carries over between embedding models; treating the
# pool as roughly normal is an approximation.
#
# Half a standard deviation above chance counts as semantic evidence; a quarter
# counts as thin evidence. On the RetailHub evaluation set every supported
# question's best match cleared +0.6 and every unsupported one stayed at or
# below +0.05.
SEMANTIC_EVIDENCE_EXCESS = 0.5
_SEMANTIC_WEAK_EXCESS = 0.25
# Fewer pooled scores than this and a standard deviation says little.
_MIN_SEPARATION_POOL = 8

# Near-duplicate detection: token-set Jaccard similarity at or above this, for
# chunks with enough tokens for the comparison to mean something. Short chunks
# are compared exactly only, so two tiny similar helpers are not collapsed.
_NEAR_DUPLICATE_JACCARD = 0.9
_NEAR_DUPLICATE_MIN_TOKENS = 12

_NAMED_PRIORITY = {"symbol": 0, "path": 1}
_CODE_TOKEN = re.compile(r"[A-Za-z0-9_]+")


def retrieve(
    session: Session,
    *,
    repository: Repository,
    query: str,
    settings: Settings,
    top_k: int | None = None,
) -> RetrievalResult:
    """Find and rank the chunks most relevant to ``query``.

    ``top_k`` truncates the fused ranking for callers that want a short list
    (the investigation tools); the Ask flow leaves it unset and lets context
    selection decide.

    Raises the same errors as the search endpoint, so callers handle one
    vocabulary for "this repository cannot be searched".
    """
    if repository.indexing_status != IndexingStatus.INDEXED:
        raise RepositoryNotIndexedError(
            "This repository has not been indexed yet.",
            details={"indexing_status": str(repository.indexing_status)},
        )

    # The index's model wins over current configuration: those are the vectors
    # that actually exist.
    model = repository.embedding_model or settings.active_embedding_model
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

    # Asymmetric embedding: the question is embedded as a query, the code was
    # embedded as documents. Using the wrong kind quietly degrades ranking.
    query_vector = provider.embed_text(query, kind=InputKind.QUERY)
    semantic_hits = embedding_repo.search_similar_chunks(
        session,
        repository_id=repository.id,
        query_vector=query_vector,
        model=model,
        top_k=settings.retrieval_semantic_candidates,
    )

    terms = lexical.query_terms(query)
    stats, lexical_available = lexical_repo.term_statistics(session, repository.id, terms.terms)
    # Terms in no chunk cannot match; terms in most chunks would flood the pool.
    searchable = [
        term
        for term in terms.terms
        if stats.document_frequency.get(term, 0) > 0 and stats.is_informative(term)
    ]
    lexical_hits = (
        lexical_repo.search_chunks(
            session,
            repository_id=repository.id,
            tsquery=lexical.build_tsquery(searchable),
            limit=settings.retrieval_lexical_candidates,
        )
        if lexical_available
        else []
    )

    in_semantic_pool = {hit.chunk_id for hit in semantic_hits}
    extra_scores = embedding_repo.similarity_for_chunks(
        session,
        chunk_ids=[hit.chunk_id for hit in lexical_hits if hit.chunk_id not in in_semantic_pool],
        query_vector=query_vector,
        model=model,
    )

    ranked = rank_candidates(
        terms=terms,
        stats=stats,
        semantic_hits=semantic_hits,
        lexical_hits=lexical_hits,
        extra_semantic_scores=extra_scores,
    )

    sources = ranked.sources
    if top_k is not None:
        sources = sources[: max(1, min(top_k, settings.search_max_top_k))]

    # Query text is user content and is never logged; only its shape is.
    logger.info(
        "Retrieval repository_id=%s query_chars=%d semantic=%d lexical=%d candidates=%d "
        "files=%d strength=%s lexical_available=%s",
        repository.id,
        len(query),
        len(semantic_hits),
        len(lexical_hits),
        len(ranked.sources),
        len({s.file_path for s in ranked.sources}),
        ranked.strength.value,
        lexical_available,
    )

    return RetrievalResult(
        sources=sources,
        strength=ranked.strength,
        model=model,
        searched_chunks=searched,
        semantic=ranked.semantic,
        lexical=ranked.lexical,
        query_terms=terms.terms,
        lexical_available=lexical_available,
        signals=ranked.signals,
    )


# ---- ranking (pure) ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RankedCandidates:
    sources: list[RetrievedSource]
    semantic: list[RetrievedSource]
    lexical: list[RetrievedSource]
    signals: RetrievalSignals
    strength: RetrievalStrength


@dataclass(slots=True)
class _Candidate:
    chunk_id: uuid.UUID
    file_path: str
    language: str
    symbol: str | None
    parent_symbol: str | None
    chunk_type: str
    start_line: int
    end_line: int
    content: str
    semantic_score: float = 0.0
    semantic_rank: int | None = None
    semantic_excess: float | None = None
    lexical_rank: int | None = None
    lexical_score: float = 0.0
    text_rank: float = 0.0
    matched_terms: tuple[str, ...] = ()
    named_as: str | None = None
    mentioned: bool = False
    exact_match: str | None = None
    fused: float = 0.0
    low_value: bool = False
    final_rank: int = 0


def rank_candidates(
    *,
    terms: lexical.QueryTerms,
    stats: lexical.TermStatistics,
    semantic_hits: Sequence[SearchHit],
    lexical_hits: Sequence[LexicalHit],
    extra_semantic_scores: dict[uuid.UUID, float] | None = None,
) -> RankedCandidates:
    """Fuse semantic and lexical hits into one ranking, and classify strength.

    Pure: no database, no provider. ``semantic_hits`` must be in similarity
    order; ``extra_semantic_scores`` supplies cosine scores for lexical hits
    outside the semantic pool.
    """
    extra_semantic_scores = extra_semantic_scores or {}
    candidates: dict[uuid.UUID, _Candidate] = {}

    for rank, hit in enumerate(semantic_hits, start=1):
        candidate = candidates.setdefault(hit.chunk_id, _candidate_from(hit))
        candidate.semantic_score = hit.score
        candidate.semantic_rank = rank

    for lexical_hit in lexical_hits:
        candidate = candidates.setdefault(lexical_hit.chunk_id, _candidate_from(lexical_hit))
        matched = tuple(
            term for term in terms.terms if term in lexical_hit.terms and stats.is_informative(term)
        )
        candidate.matched_terms = matched
        candidate.lexical_score = stats.coverage(terms.terms, set(matched))
        candidate.text_rank = lexical_hit.text_rank
        if candidate.semantic_rank is None:
            candidate.semantic_score = extra_semantic_scores.get(lexical_hit.chunk_id, 0.0)

    for candidate in candidates.values():
        candidate.low_value = is_trivia_only(candidate.content, candidate.language)
        candidate.named_as, candidate.mentioned = _exact_signals(terms.identifiers, candidate)
        if candidate.named_as:
            candidate.exact_match = candidate.named_as
        elif candidate.mentioned:
            candidate.exact_match = "identifier"

    semantic_order = sorted(
        (c for c in candidates.values() if c.semantic_rank is not None),
        key=lambda c: c.semantic_rank or 0,
    )
    semantic_evidence = [c for c in semantic_order if not c.low_value]
    for candidate, excess in zip(
        semantic_evidence,
        semantic_excess([c.semantic_score for c in semantic_evidence]),
        strict=True,
    ):
        candidate.semantic_excess = excess
    lexical_order = sorted(
        (c for c in candidates.values() if c.matched_terms),
        key=lambda c: (-c.lexical_score, -c.text_rank, str(c.chunk_id)),
    )
    for rank, candidate in enumerate(lexical_order, start=1):
        candidate.lexical_rank = rank
    named_order = sorted(
        (c for c in candidates.values() if c.named_as),
        key=lambda c: (
            _NAMED_PRIORITY[c.named_as or "path"],
            c.lexical_rank or math.inf,
            c.semantic_rank or math.inf,
        ),
    )
    mention_order = sorted(
        (c for c in candidates.values() if c.mentioned),
        key=lambda c: (c.lexical_rank or math.inf, c.semantic_rank or math.inf),
    )

    # Fusion ranks skip low-value chunks, so trivia cannot push real code down.
    for ordered in (semantic_order, lexical_order, named_order, mention_order):
        position = 0
        for candidate in ordered:
            if candidate.low_value:
                continue
            position += 1
            candidate.fused += 1.0 / (_RRF_K + position)

    fused_order = sorted(
        candidates.values(),
        key=lambda c: (-c.fused, c.semantic_rank or math.inf, str(c.chunk_id)),
    )
    kept = _deduplicate(fused_order)
    for rank, candidate in enumerate(kept, start=1):
        candidate.final_rank = rank

    signals = _signals(kept, semantic_order)
    return RankedCandidates(
        sources=[_to_source(c) for c in kept],
        semantic=[_to_source(c) for c in semantic_order],
        lexical=[_to_source(c) for c in lexical_order],
        signals=signals,
        strength=assess_strength(signals),
    )


def assess_strength(signals: RetrievalSignals) -> RetrievalStrength:
    """Classify evidence from signals that do not depend on a model's score scale.

    USEFUL when something specific is supported:
      - the question names a symbol, file or identifier that exists, or
      - a chunk is both a strong lexical match and near the top semantically
        (two independent methods agreeing), or
      - the best semantic match stands at least ``SEMANTIC_EVIDENCE_EXCESS``
        standard deviations above chance.
    WEAK when there is only thin evidence:
      - a strong lexical match that semantic retrieval does not corroborate, or
      - the best semantic match is only ``_SEMANTIC_WEAK_EXCESS`` above chance.
    NONE otherwise — including when every candidate is low-value trivia.

    A match on one incidental word ("order", "data") is deliberately not
    evidence on its own: it is exactly how an unrelated chunk looks relevant.
    """
    if signals.evidence_candidates == 0:
        return RetrievalStrength.NONE

    excess = signals.semantic_excess
    if (
        signals.exact_matches > 0
        or signals.corroborated > 0
        or (excess is not None and excess >= SEMANTIC_EVIDENCE_EXCESS)
    ):
        return RetrievalStrength.USEFUL

    if signals.strong_lexical > 0 or (excess is not None and excess >= _SEMANTIC_WEAK_EXCESS):
        return RetrievalStrength.WEAK

    return RetrievalStrength.NONE


def _signals(kept: list[_Candidate], semantic_order: list[_Candidate]) -> RetrievalSignals:
    evidence = [c for c in kept if not c.low_value]

    semantic_evidence = [c for c in semantic_order if not c.low_value]
    agreement_ids = {c.chunk_id for c in semantic_evidence[:_AGREEMENT_DEPTH]}

    exact = [c for c in evidence if c.exact_match]
    strong = [c for c in evidence if c.lexical_score >= STRONG_LEXICAL_COVERAGE]
    corroborated = [c for c in strong if c.chunk_id in agreement_ids]

    return RetrievalSignals(
        evidence_candidates=len(evidence),
        exact_matches=len(exact),
        strong_lexical=len(strong),
        partial_lexical=sum(1 for c in evidence if c.matched_terms),
        corroborated=len(corroborated),
        agreeing_files=len({c.file_path for c in exact + corroborated}),
        semantic_separation=semantic_separation([c.semantic_score for c in semantic_evidence]),
        semantic_excess=semantic_evidence[0].semantic_excess if semantic_evidence else None,
    )


def semantic_separation(scores: Sequence[float]) -> float | None:
    """``(best - mean(rest)) / stdev(rest)`` over a descending score list."""
    if len(scores) < _MIN_SEPARATION_POOL:
        return None
    best, rest = scores[0], scores[1:]
    spread = statistics.pstdev(rest)
    if spread <= 0:
        return None
    return (best - statistics.fmean(rest)) / spread


def semantic_excess(scores: Sequence[float]) -> list[float | None]:
    """Excess over chance for each score in a descending list.

    For the score at rank ``k`` of ``n``: its distance above the mean of the
    pool below the best, in that pool's standard deviations, minus the value
    the k-th largest of ``n`` independent standard normal draws is expected to
    take (Blom). Positive means "stands out more than rank alone explains".
    """
    if len(scores) < _MIN_SEPARATION_POOL:
        return [None] * len(scores)
    rest = scores[1:]
    spread = statistics.pstdev(rest)
    if spread <= 0:
        return [None] * len(scores)

    centre = statistics.fmean(rest)
    n = len(scores)
    normal = statistics.NormalDist()
    # Past the middle of the pool the expected order statistic turns negative. A
    # chunk that is merely less bad than the 30th-best of 40 unrelated chunks is
    # not evidence, so the expectation is never allowed below the pool's mean.
    return [
        (score - centre) / spread - max(normal.inv_cdf((n - k + 0.625) / (n + 0.25)), 0.0)
        for k, score in enumerate(scores, start=1)
    ]


def _exact_signals(
    identifiers: tuple[str, ...], candidate: _Candidate
) -> tuple[str | None, bool]:
    """Whether the question names this chunk (symbol/path), and whether it mentions it.

    Returns ``(named_as, mentioned)``: ``named_as`` is ``"symbol"``, ``"path"`` or
    None; ``mentioned`` is True when an identifier-shaped question token appears
    verbatim (case-insensitive) in the chunk's content.
    """
    if not identifiers:
        return None, False

    names = {_normalise(name) for name in (candidate.symbol, candidate.parent_symbol) if name}
    path = candidate.file_path.lower()
    basename = path.rsplit("/", 1)[-1]
    content = candidate.content.lower()

    named: str | None = None
    mentioned = False
    for token in identifiers:
        lowered = token.lower()
        if _normalise(token) in names:
            named = "symbol"
        stripped = lowered.strip("/")
        if (
            named is None
            and ("." in stripped or "/" in stripped)
            and (path == stripped or path.endswith("/" + stripped) or basename == stripped)
        ):
            named = "path"
        if lowered in content:
            mentioned = True
    return named, mentioned


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _candidate_from(hit: SearchHit | LexicalHit) -> _Candidate:
    return _Candidate(
        chunk_id=hit.chunk_id,
        file_path=hit.file_path,
        language=hit.language,
        symbol=hit.symbol,
        parent_symbol=hit.parent_symbol,
        chunk_type=hit.chunk_type,
        start_line=hit.start_line,
        end_line=hit.end_line,
        content=hit.content,
    )


def _to_source(candidate: _Candidate) -> RetrievedSource:
    return RetrievedSource(
        rank=candidate.final_rank,
        chunk_id=str(candidate.chunk_id),
        file_path=candidate.file_path,
        language=candidate.language,
        symbol=candidate.symbol,
        parent_symbol=candidate.parent_symbol,
        chunk_type=candidate.chunk_type,
        start_line=candidate.start_line,
        end_line=candidate.end_line,
        content=candidate.content,
        score=candidate.semantic_score,
        semantic_rank=candidate.semantic_rank,
        semantic_excess=None
        if candidate.semantic_excess is None
        else round(candidate.semantic_excess, 3),
        lexical_rank=candidate.lexical_rank,
        lexical_score=round(candidate.lexical_score, 4),
        matched_terms=candidate.matched_terms,
        exact_match=candidate.exact_match,
        fused_score=round(candidate.fused, 6),
        low_value=candidate.low_value,
    )


class _HasContent(Protocol):
    @property
    def content(self) -> str: ...


_T = TypeVar("_T", bound=_HasContent)


def _deduplicate(items: Sequence[_T]) -> list[_T]:
    """Drop chunks whose text repeats, or nearly repeats, one already kept.

    Split symbols and copied code produce identical or near-identical chunks;
    sending the same body twice wastes context budget a different file could
    use. Order is preserved, so the best-ranked copy is the one retained.
    """
    seen: set[str] = set()
    kept_token_sets: list[frozenset[str]] = []
    unique: list[_T] = []

    for item in items:
        # Whitespace-insensitive so reformatting does not defeat the check.
        fingerprint = " ".join(item.content.split())
        if fingerprint in seen:
            continue

        tokens = frozenset(_CODE_TOKEN.findall(item.content))
        if len(tokens) >= _NEAR_DUPLICATE_MIN_TOKENS and any(
            len(other) >= _NEAR_DUPLICATE_MIN_TOKENS
            and len(tokens & other) / len(tokens | other) >= _NEAR_DUPLICATE_JACCARD
            for other in kept_token_sets
        ):
            continue

        seen.add(fingerprint)
        kept_token_sets.append(tokens)
        unique.append(item)

    return unique
