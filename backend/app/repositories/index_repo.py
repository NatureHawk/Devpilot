"""Queries over indexed content: ``files`` and ``code_chunks``."""

from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.orm import Session

from app.models.source import SourceFile


def delete_repository_index(session: Session, repository_id: uuid.UUID) -> int:
    """Remove every indexed file for a repository.

    Chunks go with them via ``ON DELETE CASCADE``. Called inside the same
    transaction that writes the replacement, so the previous index survives
    untouched if that transaction rolls back.
    """
    result = cast(
        CursorResult[Any],
        session.execute(delete(SourceFile).where(SourceFile.repository_id == repository_id)),
    )
    return result.rowcount or 0


def language_breakdown(session: Session, repository_id: uuid.UUID) -> list[tuple[str, int]]:
    """File counts per language, for the workspace summary.

    Served by ``ix_files_repository_id_language``.
    """
    stmt = (
        select(SourceFile.language, func.count())
        .where(SourceFile.repository_id == repository_id, SourceFile.language.is_not(None))
        .group_by(SourceFile.language)
        .order_by(func.count().desc())
    )
    return [(str(language), count) for language, count in session.execute(stmt)]
