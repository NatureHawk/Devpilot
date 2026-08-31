"""Builds the text that represents a chunk to the embedding model.

Raw source alone embeds poorly for retrieval: a question like "where is
authentication handled" names a concept, while the code may only contain
``def _verify(...)``. Prefixing the location and symbol gives the model the
vocabulary the question is likely to use.

The representation is a pure function of stored chunk fields — deterministic, so
re-embedding an unchanged chunk yields an identical vector, and cheap, so there
is no LLM in the indexing path.
"""

from __future__ import annotations

import hashlib

# Guards the assembled string. Chunk content is already bounded by
# INDEX_MAX_CHUNK_CHARS; this covers the header and any future additions.
MAX_EMBEDDING_TEXT_CHARS = 32_000


def build_embedding_text(
    *,
    repository_full_name: str,
    file_path: str,
    language: str,
    chunk_type: str,
    symbol: str | None,
    parent_symbol: str | None,
    content: str,
) -> str:
    """Compose the embedded representation of one chunk.

    The header lines are stable and ordered; changing them changes every vector,
    so any edit here requires a re-index to stay comparable.
    """
    lines = [
        f"Repository: {repository_full_name}",
        f"Path: {file_path}",
        f"Language: {language}",
        f"Kind: {chunk_type}",
    ]

    # A method is far more findable when the class name is present, since the
    # class is usually what a question names.
    if parent_symbol and symbol:
        lines.append(f"Symbol: {parent_symbol}.{symbol}")
    elif symbol:
        lines.append(f"Symbol: {symbol}")
    if parent_symbol:
        lines.append(f"Parent: {parent_symbol}")

    lines.append("")
    lines.append(content)

    return "\n".join(lines)[:MAX_EMBEDDING_TEXT_CHARS]


def content_hash(embedding_text: str) -> str:
    """Identity of a chunk for embedding-reuse purposes.

    A re-index generates fresh chunk rows (fresh ids) even when nothing about
    a file changed, so ``chunk_id`` cannot be what identifies "the same
    chunk" across two runs — this can. Hashing the exact text sent to the
    provider (rather than just ``content``) means any change that would
    actually change the vector — content, symbol, path, language — correctly
    invalidates reuse; anything else correctly does not.
    """
    return hashlib.sha256(embedding_text.encode("utf-8")).hexdigest()
