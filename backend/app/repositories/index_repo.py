"""Queries over indexed content: ``files`` and ``code_chunks``."""

from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.orm import Session

from app.models.source import CodeChunk, SourceFile


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


def count_files(session: Session, repository_id: uuid.UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(SourceFile)
        .where(SourceFile.repository_id == repository_id)
    )
    return session.scalar(stmt) or 0


def count_parsed_files(session: Session, repository_id: uuid.UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(SourceFile)
        .where(SourceFile.repository_id == repository_id, SourceFile.is_parsed.is_(True))
    )
    return session.scalar(stmt) or 0


def count_chunks(session: Session, repository_id: uuid.UUID) -> int:
    stmt = (
        select(func.count()).select_from(CodeChunk).where(CodeChunk.repository_id == repository_id)
    )
    return session.scalar(stmt) or 0


def list_files(
    session: Session, repository_id: uuid.UUID, *, limit: int = 100, offset: int = 0
) -> list[SourceFile]:
    stmt = (
        select(SourceFile)
        .where(SourceFile.repository_id == repository_id)
        .order_by(SourceFile.path)
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(stmt))


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
