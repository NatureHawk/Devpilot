"""Indexed repository content: source files and the chunks parsed out of them."""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey


class ChunkType(enum.StrEnum):
    """Normalised structural kind of a chunk.

    Deliberately small. Language-specific detail lives in ``node_type`` rather
    than expanding this into a cross-language taxonomy that no language fits.
    """

    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    # Line-window fallback: a file with no parseable structure, or the
    # continuation pieces of a symbol too large to store whole.
    BLOCK = "block"


class SourceFile(Base, UUIDPrimaryKey, Timestamps):
    """One indexed file from a repository snapshot.

    Content is stored inline rather than re-fetched from GitHub on every read:
    the size limit keeps rows bounded, and retrieval needs the exact bytes that
    were parsed, not whatever the branch holds later.
    """

    __tablename__ = "files"
    __table_args__ = (
        # One row per path per repository — re-indexing replaces, never appends.
        UniqueConstraint("repository_id", "path", name="repository_id_path"),
        # Supports "which files in this repository are in language X".
        Index("ix_files_repository_id_language", "repository_id", "language"),
    )

    repository_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(String(1000), nullable=False)
    blob_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Detected language slug, or NULL for a stored-but-unclassified text file.
    language: Mapped[str | None] = mapped_column(String(50))
    content_type: Mapped[str | None] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # False when the language has no parser, or when parsing failed. A file is
    # still stored and searchable as text either way.
    is_parsed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Human-readable reason a supported file produced no structure. Non-null
    # here is what makes "partially indexed" visible instead of silent.
    parse_error: Mapped[str | None] = mapped_column(Text)

    chunks: Mapped[list[CodeChunk]] = relationship(
        back_populates="file", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<SourceFile {self.path}>"


class CodeChunk(Base, UUIDPrimaryKey, Timestamps):
    """A syntax-aware slice of a file, sized to stand on its own.

    Carries enough metadata to render ``path -> symbol -> lines -> source``
    without reparsing anything.
    """

    __tablename__ = "code_chunks"
    __table_args__ = (
        # Reading a file's chunks in source order.
        Index("ix_code_chunks_file_id_start_line", "file_id", "start_line"),
        # Repository-wide filters, e.g. "every function in this repository".
        Index("ix_code_chunks_repository_id_chunk_type", "repository_id", "chunk_type"),
    )

    file_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalised from files.repository_id: retrieval always filters by
    # repository, and this avoids a join on the hottest future query path.
    repository_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )

    chunk_type: Mapped[ChunkType] = mapped_column(
        Enum(ChunkType, name="chunk_type", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    # Raw grammar node type, e.g. "arrow_function" or "class_declaration".
    # Keeps language nuance without widening ChunkType.
    node_type: Mapped[str | None] = mapped_column(String(100))
    symbol: Mapped[str | None] = mapped_column(String(300))
    # Enclosing symbol, e.g. the class a method belongs to. Context by reference
    # rather than copying the parent body into every child chunk.
    parent_symbol: Mapped[str | None] = mapped_column(String(300))

    # 1-based and inclusive, matching how editors and GitHub display lines.
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    start_byte: Mapped[int] = mapped_column(Integer, nullable=False)
    end_byte: Mapped[int] = mapped_column(Integer, nullable=False)

    language: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Position within a symbol that had to be split; 1/1 when whole.
    part_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    part_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    file: Mapped[SourceFile] = relationship(back_populates="chunks")

    def __repr__(self) -> str:
        return f"<CodeChunk {self.chunk_type}:{self.symbol} {self.start_line}-{self.end_line}>"
