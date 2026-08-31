"""Semantic retrieval over a repository's indexed chunks.

Embeds the question, ranks stored chunk vectors by cosine similarity, and
returns the nearest chunks with their exact locations. No language model is
involved: this milestone retrieves code, it does not generate prose about it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import status
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.models.repository import IndexingStatus, Repository
from app.repositories import embedding_repo
from app.repositories.embedding_repo import SearchHit
from app.services.embeddings import InputKind, get_provider

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


@dataclass(slots=True)
class SearchResponse:
    results: list[SearchHit]
    model: str
    query_chunk_count: int


def search_repository(
    session: Session,
    *,
    repository: Repository,
    query: str,
    top_k: int,
    settings: Settings,
) -> SearchResponse:
    """Return the chunks most similar to ``query``.

    ``top_k`` is validated by the schema; the model used for search is the one
    recorded on the repository, so a configuration change cannot silently
    compare vectors that were produced by different models.
    """
    if repository.indexing_status != IndexingStatus.INDEXED:
        raise RepositoryNotIndexedError(
            "This repository has not been indexed yet.",
            details={"indexing_status": str(repository.indexing_status)},
        )

    # The index's model wins over current configuration: those are the vectors
    # that actually exist.
    model = repository.embedding_model or settings.active_embedding_model
    stored = embedding_repo.count_embeddings(session, repository.id, model=model)
    if stored == 0:
        raise RepositoryNotSearchableError(
            "This repository has no embeddings for the active model. Re-index it to enable search.",
            details={"model": model},
        )

    provider = get_provider(settings)
    if provider.model != model:
        raise RepositoryNotSearchableError(
            "This repository was indexed with a different embedding model. Re-index it to search.",
            details={"indexed_with": model, "configured": provider.model},
        )

    # Asymmetric embedding: the question is embedded as a query, the code was
    # embedded as documents. Using the wrong kind quietly degrades ranking.
    query_vector = provider.embed_text(query, kind=InputKind.QUERY)

    hits = embedding_repo.search_similar_chunks(
        session,
        repository_id=repository.id,
        query_vector=query_vector,
        model=model,
        top_k=top_k,
    )

    # Query text is user content and is never logged; only its shape is.
    logger.info(
        "Repository search repository_id=%s query_chars=%d top_k=%d results=%d model=%s",
        repository.id,
        len(query),
        top_k,
        len(hits),
        model,
    )

    return SearchResponse(results=hits, model=model, query_chunk_count=stored)
