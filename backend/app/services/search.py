"""Retrieval inspection over a repository's indexed chunks.

Runs exactly the retrieval and context selection an answer would get — semantic
candidates, lexical candidates, the fused ranking and the sources that fit the
budget — and stops there. No language model is involved.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.repository import Repository
from app.services.context_builder import BuiltContext, build_context
from app.services.retrieval import (
    RepositoryNotIndexedError,
    RepositoryNotSearchableError,
    RetrievalResult,
    retrieve,
)

__all__ = [
    "RepositoryNotIndexedError",
    "RepositoryNotSearchableError",
    "SearchOutcome",
    "search_repository",
]

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SearchOutcome:
    retrieval: RetrievalResult
    context: BuiltContext
    max_sources: int


def search_repository(
    session: Session,
    *,
    repository: Repository,
    query: str,
    settings: Settings,
    max_sources: int | None = None,
) -> SearchOutcome:
    """Retrieve and select evidence for ``query`` as the Ask flow would."""
    retrieval = retrieve(session, repository=repository, query=query, settings=settings)

    limit = max_sources or settings.context_max_sources
    context = build_context(
        repository_full_name=f"{repository.owner}/{repository.name}",
        retrieval=retrieval,
        max_chars=settings.effective_context_max_chars,
        max_sources=limit,
    )

    # Query text is user content and is never logged; only its shape is.
    logger.info(
        "Repository search repository_id=%s query_chars=%d candidates=%d selected=%d "
        "source_chars=%d strength=%s",
        repository.id,
        len(query),
        len(retrieval.sources),
        len(context.included),
        context.source_chars,
        retrieval.strength.value,
    )

    return SearchOutcome(retrieval=retrieval, context=context, max_sources=limit)
