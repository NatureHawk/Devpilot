"""Lexical terms for PostgreSQL full-text search over code.

Semantic retrieval matches meaning; it is unreliable for exact identifiers.
"Where is useMemo used?" or "what does get_db do?" name a token that either
appears in the code or does not, and that is a lexical question.

PostgreSQL's parser is built for prose, and on code it is inconsistent: with
the ``simple`` configuration it splits ``get_db`` into ``get``/``db``, keeps
``useMemo`` as one token, and treats ``/analytics/operations`` and ``App.jsx``
as single file/host tokens. So terms are normalised here, in one deterministic
function shared by indexing and querying, and PostgreSQL only ever sees plain
``[a-z0-9]`` words:

    get_db                -> getdb get db
    useMemo               -> usememo use memo
    /analytics/operations -> analytics operations
    HTTPException         -> httpexception http exception

The whole identifier (joined) is kept alongside its parts, so an exact
identifier matches strongly while its parts still match partial questions.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# An identifier as most languages define it. `$` covers JavaScript names.
_IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
# camelCase / PascalCase / acronym boundaries (useMemo, HTTPException, parseJSON)
# and letter/digit boundaries (sqlite3 -> sqlite, h264 -> h).
_CAMEL_BOUNDARY = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])"
)
_NON_TERM = re.compile(r"[^a-z0-9]")
_NON_WORD = re.compile(r"[^A-Za-z0-9]+")
# A query token as typed, before normalisation: keeps `_ . / -` so shape survives.
_QUERY_TOKEN = re.compile(r"[A-Za-z0-9_$./-]+")

_MIN_PART_CHARS = 2
_MAX_TERM_CHARS = 64

# Question words that carry no lexical signal. Deliberately small: anything that
# can plausibly be a code identifier ("use", "get", "data", "app") stays, and
# inverse document frequency discounts common terms on its own.
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "any", "are", "as", "at", "be", "by", "can", "do", "does",
        "done", "for", "from", "has", "have", "how", "i", "in", "into", "is", "it",
        "its", "me", "my", "of", "on", "or", "our", "should", "so", "that", "the",
        "their", "them", "there", "these", "this", "those", "to", "used", "was",
        "we", "were", "what", "when", "where", "which", "who", "why", "will",
        "with", "would", "you", "your", "defined", "implemented", "handled",
        "happens", "work", "works",
    }
)  # fmt: skip


def identifier_terms(identifier: str) -> list[str]:
    """Normalised terms for one identifier: the joined whole, then its parts."""
    whole = _NON_TERM.sub("", identifier.lower())[:_MAX_TERM_CHARS]
    terms = [whole] if whole else []

    for piece in re.split(r"[_$]+", identifier):
        for part in _CAMEL_BOUNDARY.split(piece):
            normalised = _NON_TERM.sub("", part.lower())[:_MAX_TERM_CHARS]
            if len(normalised) >= _MIN_PART_CHARS and normalised != whole:
                terms.append(normalised)

    return terms


def text_terms(text: str) -> list[str]:
    """Terms for arbitrary source or prose, in order, repeats kept.

    Repeats are kept on purpose: term frequency is part of how PostgreSQL ranks
    a match.
    """
    terms: list[str] = []
    for match in _IDENTIFIER.finditer(text):
        terms.extend(identifier_terms(match.group()))
    return terms


def chunk_lexical_fields(
    *, file_path: str, symbol: str | None, parent_symbol: str | None, content: str
) -> tuple[str, str]:
    """The two stored term strings for a chunk: ``(names, body)``.

    ``names`` (path, symbol, parent) is weighted above ``body`` in the
    tsvector, so a chunk *named* after a query term outranks one that merely
    mentions it.
    """
    name_parts = [file_path]
    if parent_symbol:
        name_parts.append(parent_symbol)
    if symbol:
        name_parts.append(symbol)
    names = " ".join(text_terms(" ".join(name_parts)))
    body = " ".join(text_terms(content))
    return names, body


@dataclass(frozen=True, slots=True)
class QueryTerms:
    """What a question contributes to lexical search."""

    # Normalised, stopwords removed, de-duplicated, in order of appearance.
    terms: tuple[str, ...]
    # Tokens that look like code as the user typed them — get_db, useMemo,
    # /analytics/operations, App.jsx, sqlite3 — matched verbatim for exact signals.
    identifiers: tuple[str, ...]


def query_terms(query: str) -> QueryTerms:
    terms: list[str] = []
    identifiers: list[str] = []

    for raw in _QUERY_TOKEN.findall(query):
        token = raw.strip("./-")
        if not token:
            continue
        if _looks_like_code(token):
            identifiers.append(token)
            token_terms = text_terms(token)
        else:
            # A plain word is not split into camel parts: "SQLite" is one idea,
            # and "sq"/"lite" would only dilute the question's term weight.
            token_terms = [
                word
                for word in (_NON_TERM.sub("", part.lower()) for part in _NON_WORD.split(token))
                if word
            ]
        for term in token_terms:
            if term not in _STOPWORDS and term not in terms:
                terms.append(term)

    return QueryTerms(terms=tuple(terms), identifiers=tuple(dict.fromkeys(identifiers)))


def _looks_like_code(token: str) -> bool:
    """Whether a token is identifier- or path-shaped rather than a plain word."""
    if "_" in token or "/" in token:
        return True
    if re.search(r"[a-z][A-Z]", token):  # camelCase
        return True
    if re.search(r"[A-Za-z]\.[A-Za-z]", token):  # file.ext, module.attr
        return True
    return bool(re.search(r"[A-Za-z]", token) and re.search(r"[0-9]", token))


def build_tsquery(terms: list[str] | tuple[str, ...]) -> str:
    """OR-query over already-normalised terms.

    Safe to hand to ``to_tsquery``: every term is ``[a-z0-9]+`` by construction,
    so no operator or quote can reach the query parser.
    """
    return " | ".join(term for term in terms if term and not _NON_TERM.search(term))


@dataclass(frozen=True, slots=True)
class TermStatistics:
    """Document frequencies of query terms within one repository's chunks."""

    total_chunks: int
    document_frequency: dict[str, int] = field(default_factory=dict)

    def idf(self, term: str) -> float:
        """BM25 inverse document frequency, always positive.

        ``ln(1 + (N - df + 0.5) / (df + 0.5))`` — the standard Robertson/Spärck
        Jones form used by BM25. A term in no chunk gets the maximum weight, a
        term in every chunk approaches zero.
        """
        df = self.document_frequency.get(term, 0)
        n = max(self.total_chunks, 1)
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))

    def is_informative(self, term: str) -> bool:
        """False for terms present in more than half the chunks.

        Half is BM25's neutral point — beyond it, classic idf turns negative,
        meaning a match says more about the corpus than about the question.
        Such terms are left out of the database query so a word like "data"
        cannot flood the candidate pool.
        """
        df = self.document_frequency.get(term, 0)
        return df * 2 <= max(self.total_chunks, 1)

    def coverage(self, query: tuple[str, ...], matched: set[str] | frozenset[str]) -> float:
        """Share of the query's informative idf weight that a chunk matches, in [0, 1].

        Terms absent from the repository stay in the denominator: a question
        about "stripe webhooks" in a repository with neither word is not
        well covered by a chunk that happens to contain "processing".
        """
        weighted = [(term, self.idf(term)) for term in query if self.is_informative(term)]
        total = sum(weight for _, weight in weighted)
        if total <= 0:
            return 0.0
        return sum(weight for term, weight in weighted if term in matched) / total
