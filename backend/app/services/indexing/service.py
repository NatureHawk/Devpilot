"""Indexing orchestration.

    repository -> tree -> filter -> download -> detect -> parse -> chunk -> persist

Each stage lives in its own module; this file only sequences them and owns the
transaction boundaries. The route calls :func:`index_repository` and nothing
else.

Two transactions, deliberately:

1. Mark the repository ``indexing`` and commit, so a concurrent request can see
   a run is under way and refuse to start a second one.
2. Delete the old rows, stream the new ones in, and mark the repository
   ``indexed`` — all in one transaction. The delete only becomes durable if the
   whole run commits, so a failure leaves the previous index exactly as it was.

Repository content is data, never code: it is downloaded, decoded, parsed by
Tree-sitter and stored. Nothing from a repository is executed, imported, or
passed to a shell.
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, ConflictError
from app.integrations.github.client import GitHubClient
from app.integrations.github.errors import GitHubError
from app.integrations.github.models import TreeEntry
from app.models.repository import IndexingStatus, Repository
from app.models.source import ChunkType, CodeChunk, SourceFile
from app.repositories import index_repo
from app.services.indexing.chunker import ChunkingLimits, chunk_file
from app.services.indexing.filters import (
    FileFilter,
    FilterLimits,
    SkipReason,
    decode_text,
    looks_binary,
)
from app.services.indexing.languages import Language
from app.services.indexing.parser import ParsedFile, ParserUnavailableError, parse_file

logger = logging.getLogger(__name__)

# A run older than this is treated as abandoned — the process died mid-index —
# so a retry is allowed instead of the repository being stuck in "indexing".
STALE_RUN_MINUTES = 30

# Flush to the database every N files so Python-side memory stays flat instead of
# growing with the repository. The rows live in the open transaction, not here.
_FLUSH_EVERY_FILES = 25


class IndexingInProgressError(ConflictError):
    code = "indexing_in_progress"


class IndexingFailedError(AppError):
    status_code = 500
    code = "indexing_failed"


@dataclass(slots=True)
class IndexingReport:
    """What one indexing run did. Returned to the API verbatim."""

    repository_id: uuid.UUID
    status: IndexingStatus
    commit_sha: str | None = None
    files_discovered: int = 0
    files_indexed: int = 0
    files_parsed: int = 0
    chunks_created: int = 0
    files_skipped: int = 0
    skipped_by_reason: dict[str, int] = field(default_factory=dict)
    parse_failures: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None

    @property
    def complete(self) -> bool:
        """True when every file that should have parsed did parse.

        Skipped files do not make an index incomplete — they were never meant to
        be included. A parse failure does.
        """
        return self.parse_failures == 0


@dataclass(slots=True)
class _PreparedFile:
    """One downloaded file, ready to persist."""

    path: str
    blob_sha: str
    language: str
    content_type: str
    text: str
    size_bytes: int
    parsed: ParsedFile | None
    parse_error: str | None


def index_repository(
    session: Session,
    *,
    repository: Repository,
    client: GitHubClient,
    settings: Settings,
) -> IndexingReport:
    """Index one repository, synchronously.

    The caller waits for the whole run: this milestone has no worker
    infrastructure, and pretending otherwise would be a lie in the API.
    """
    _claim_run(session, repository)

    started_at = repository.indexing_started_at or datetime.now(UTC)
    report = IndexingReport(
        repository_id=repository.id, status=IndexingStatus.INDEXING, started_at=started_at
    )

    logger.info(
        "Indexing started repository_id=%s repository=%s/%s",
        repository.id,
        repository.owner,
        repository.name,
    )

    try:
        _run(session, repository=repository, client=client, settings=settings, report=report)
    except Exception as exc:
        session.rollback()
        _record_failure(session, repository, exc)
        report.status = IndexingStatus.FAILED
        report.error = _safe_message(exc)
        report.completed_at = repository.indexed_at
        logger.warning(
            "Indexing failed repository_id=%s reason=%s", repository.id, type(exc).__name__
        )
        # GitHub and application errors already carry an accurate code; anything
        # else becomes indexing_failed rather than a bare 500.
        if isinstance(exc, AppError):
            raise
        raise IndexingFailedError(_safe_message(exc)) from exc

    logger.info(
        "Indexing completed repository_id=%s commit=%s files=%d parsed=%d chunks=%d "
        "skipped=%d parse_failures=%d",
        repository.id,
        report.commit_sha,
        report.files_indexed,
        report.files_parsed,
        report.chunks_created,
        report.files_skipped,
        report.parse_failures,
    )
    return report


def _claim_run(session: Session, repository: Repository) -> None:
    """Move the repository into ``indexing``, refusing a concurrent second run."""
    if repository.indexing_status == IndexingStatus.INDEXING and not _is_stale(repository):
        raise IndexingInProgressError(
            "This repository is already being indexed.",
            details={"repository_id": str(repository.id)},
        )

    repository.indexing_status = IndexingStatus.INDEXING
    repository.indexing_started_at = datetime.now(UTC)
    repository.indexing_error = None
    session.commit()


def _is_stale(repository: Repository) -> bool:
    started = repository.indexing_started_at
    if started is None:
        return True
    elapsed = datetime.now(UTC) - started
    return elapsed.total_seconds() > STALE_RUN_MINUTES * 60


def _run(
    session: Session,
    *,
    repository: Repository,
    client: GitHubClient,
    settings: Settings,
    report: IndexingReport,
) -> None:
    owner, name = repository.owner, repository.name

    # ---- discovery -------------------------------------------------------
    commit_sha = client.get_branch_head_sha(owner, name, repository.default_branch)
    tree = client.get_full_tree(owner, name, commit_sha)
    report.commit_sha = commit_sha
    report.files_discovered = len(tree.entries)
    logger.info(
        "Files discovered repository_id=%s count=%d truncated=%s",
        repository.id,
        report.files_discovered,
        tree.truncated,
    )

    # ---- filtering -------------------------------------------------------
    file_filter = FileFilter(
        FilterLimits(
            max_file_bytes=settings.index_max_file_bytes,
            max_total_bytes=settings.index_max_total_bytes,
            max_files=settings.index_max_files,
        )
    )
    accepted, skipped = _select_files(tree.entries, file_filter)
    report.skipped_by_reason = dict(skipped)
    report.files_skipped = sum(skipped.values())
    logger.info(
        "Files skipped repository_id=%s count=%d reasons=%s",
        repository.id,
        report.files_skipped,
        report.skipped_by_reason,
    )

    by_sha = {entry.sha: (entry, language) for entry, language in accepted}
    chunking_limits = ChunkingLimits(
        max_chunk_chars=settings.index_max_chunk_chars,
        fallback_chunk_lines=settings.index_fallback_chunk_lines,
    )

    # ---- persistence -----------------------------------------------------
    # The old index is removed inside this transaction, so it is only really
    # gone once the replacement commits.
    index_repo.delete_repository_index(session, repository.id)

    seen_paths: set[str] = set()
    pending = 0

    for blob_sha, result in client.get_blobs(owner, name, list(by_sha)):
        selected = by_sha.get(blob_sha)
        if selected is None:
            continue
        entry, language = selected

        if isinstance(result, Exception):
            # One unreadable blob does not end the run; it is counted as a
            # skip so the report still reflects reality.
            _count_skip(report, SkipReason.DECODE_FAILED)
            logger.warning(
                "Blob download failed repository_id=%s path=%s reason=%s",
                repository.id,
                entry.path,
                type(result).__name__,
            )
            continue

        prepared = _prepare_file(entry, result, language=language)
        if isinstance(prepared, SkipReason):
            _count_skip(report, prepared)
            continue

        # Identical paths cannot occur in a git tree, but a defensive guard
        # keeps the unique constraint from turning a surprise into a 500.
        if prepared.path in seen_paths:
            continue
        seen_paths.add(prepared.path)

        _persist_file(
            session,
            repository=repository,
            prepared=prepared,
            limits=chunking_limits,
            report=report,
        )

        pending += 1
        if pending >= _FLUSH_EVERY_FILES:
            session.flush()
            # Detach persisted objects so the identity map does not accumulate
            # the whole repository in memory.
            session.expunge_all()
            pending = 0

    session.flush()

    # ---- completion ------------------------------------------------------
    repository = session.merge(repository)
    completed_at = datetime.now(UTC)
    repository.indexing_status = IndexingStatus.INDEXED
    repository.indexed_at = completed_at
    repository.indexed_commit_sha = commit_sha
    repository.indexing_error = None
    repository.indexed_file_count = report.files_indexed
    repository.indexed_parsed_file_count = report.files_parsed
    repository.indexed_chunk_count = report.chunks_created

    session.commit()

    report.status = IndexingStatus.INDEXED
    report.completed_at = completed_at


def _select_files(
    entries: list[TreeEntry], file_filter: FileFilter
) -> tuple[list[tuple[TreeEntry, Language]], Counter[str]]:
    """Apply the filter to every entry, tracking budget as files are accepted.

    The detected language is carried forward with each accepted entry so the
    download stage never has to re-run the filter to recover it.
    """
    accepted: list[tuple[TreeEntry, Language]] = []
    skipped: Counter[str] = Counter()
    accepted_bytes = 0

    for entry in entries:
        decision = file_filter.evaluate(
            entry, accepted_files=len(accepted), accepted_bytes=accepted_bytes
        )
        if decision.include and decision.language is not None:
            accepted.append((entry, decision.language))
            accepted_bytes += entry.size or 0
        elif decision.reason is not None:
            skipped[str(decision.reason)] += 1

    return accepted, skipped


def _prepare_file(
    entry: TreeEntry, content: bytes, *, language: Language
) -> _PreparedFile | SkipReason:
    """Validate and parse one downloaded blob."""
    if looks_binary(content):
        return SkipReason.BINARY

    text = decode_text(content)
    if text is None:
        return SkipReason.DECODE_FAILED
    if not text.strip():
        return SkipReason.EMPTY

    parsed: ParsedFile | None = None
    parse_error: str | None = None

    if language.parseable:
        try:
            parsed = parse_file(content, language.slug)
        except ParserUnavailableError:
            # The registry claims parseability the parser does not provide;
            # recorded rather than hidden.
            parse_error = "No parser is configured for this language."
        except Exception as exc:
            parse_error = f"Parsing failed ({type(exc).__name__})."
        else:
            if parsed.has_errors and not parsed.symbols:
                # Syntax errors that yielded nothing usable: the file is still
                # stored and chunked by lines, but it is not "parsed".
                parse_error = "The file could not be parsed cleanly."

    return _PreparedFile(
        path=entry.path,
        blob_sha=entry.sha,
        language=language.slug,
        content_type=language.content_type,
        text=text,
        size_bytes=len(content),
        parsed=parsed,
        parse_error=parse_error,
    )


def _persist_file(
    session: Session,
    *,
    repository: Repository,
    prepared: _PreparedFile,
    limits: ChunkingLimits,
    report: IndexingReport,
) -> None:
    source_bytes = prepared.text.encode("utf-8")
    is_parsed = prepared.parsed is not None and prepared.parse_error is None

    source_file = SourceFile(
        repository_id=repository.id,
        path=prepared.path,
        blob_sha=prepared.blob_sha,
        size_bytes=prepared.size_bytes,
        line_count=prepared.text.count("\n") + 1,
        language=prepared.language,
        content_type=prepared.content_type,
        content=prepared.text,
        is_parsed=is_parsed,
        parse_error=prepared.parse_error,
    )
    session.add(source_file)
    # The chunk rows need the file's id, which is generated application-side, so
    # no round trip is required here.
    session.flush([source_file])

    drafts = chunk_file(source_bytes, prepared.parsed, limits=limits)
    for draft in drafts:
        session.add(
            CodeChunk(
                file_id=source_file.id,
                repository_id=repository.id,
                chunk_type=ChunkType(draft.chunk_type),
                node_type=draft.node_type,
                symbol=draft.symbol,
                parent_symbol=draft.parent_symbol,
                start_line=draft.start_line,
                end_line=draft.end_line,
                start_byte=draft.start_byte,
                end_byte=draft.end_byte,
                language=prepared.language,
                content=draft.content,
                part_index=draft.part_index,
                part_count=draft.part_count,
            )
        )

    report.files_indexed += 1
    report.chunks_created += len(drafts)
    if is_parsed:
        report.files_parsed += 1
    elif prepared.parse_error is not None:
        report.parse_failures += 1


def _count_skip(report: IndexingReport, reason: SkipReason) -> None:
    report.files_skipped += 1
    report.skipped_by_reason[str(reason)] = report.skipped_by_reason.get(str(reason), 0) + 1


def _record_failure(session: Session, repository: Repository, exc: Exception) -> None:
    """Mark the run failed without touching previously indexed rows."""
    repository = session.merge(repository)
    repository.indexing_status = IndexingStatus.FAILED
    repository.indexing_error = _safe_message(exc)
    session.commit()


def _safe_message(exc: Exception) -> str:
    """A message safe to store and show.

    Application and GitHub errors already carry vetted text. Anything else is
    reduced to its type: driver and library messages can embed connection
    strings, URLs and tokens.
    """
    if isinstance(exc, GitHubError | AppError):
        return exc.message
    return f"Indexing failed unexpectedly ({type(exc).__name__})."
