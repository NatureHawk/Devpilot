"""Lexical term normalisation and term statistics. Pure; no database."""

from __future__ import annotations

import pytest

from app.services.lexical import (
    TermStatistics,
    build_tsquery,
    chunk_lexical_fields,
    identifier_terms,
    query_terms,
    text_terms,
)


class TestIdentifierTerms:
    @pytest.mark.parametrize(
        ("identifier", "expected"),
        [
            ("get_db", ["getdb", "get", "db"]),
            ("useMemo", ["usememo", "use", "memo"]),
            ("DB_PATH", ["dbpath", "db", "path"]),
            ("HTTPException", ["httpexception", "http", "exception"]),
            ("sqlite3", ["sqlite3", "sqlite"]),
        ],
    )
    def test_keeps_the_joined_identifier_and_its_parts(
        self, identifier: str, expected: list[str]
    ) -> None:
        assert identifier_terms(identifier) == expected

    def test_terms_are_plain_lowercase_alphanumerics(self) -> None:
        """PostgreSQL only ever sees [a-z0-9] words, never parser-sensitive tokens."""
        for term in text_terms("axios.get(`${API_BASE}/analytics/operations`) x-api-key"):
            assert term.isalnum() and term == term.lower()

    def test_repeats_are_kept_for_term_frequency(self) -> None:
        assert text_terms("conn conn").count("conn") == 2


class TestChunkFields:
    def test_names_carry_path_and_symbol_and_body_carries_content(self) -> None:
        names, body = chunk_lexical_fields(
            file_path="backend/api.py",
            symbol="get_db",
            parent_symbol=None,
            content="def get_db():\n    conn = sqlite3.connect(DB_PATH)",
        )

        assert {"backend", "api", "py", "getdb"} <= set(names.split())
        assert {"sqlite3", "connect", "dbpath", "conn"} <= set(body.split())


class TestQueryTerms:
    def test_code_identifiers_are_recognised_as_typed(self) -> None:
        terms = query_terms("Where is useMemo used?")

        assert terms.terms == ("usememo", "use", "memo")
        assert terms.identifiers == ("useMemo",)

    def test_snake_case_identifier(self) -> None:
        terms = query_terms("Where is get_db defined?")

        assert terms.terms == ("getdb", "get", "db")
        assert terms.identifiers == ("get_db",)

    def test_route_paths_are_identifiers(self) -> None:
        terms = query_terms("Where is /analytics/operations defined?")

        assert terms.terms == ("analytics", "operations")
        assert terms.identifiers == ("analytics/operations",)

    def test_file_names_are_identifiers(self) -> None:
        assert query_terms("what does App.jsx render").identifiers == ("App.jsx",)

    def test_plain_words_are_terms_but_not_identifiers(self) -> None:
        terms = query_terms("Where is SQLite configured?")

        assert "sqlite" in terms.terms
        assert "configured" in terms.terms
        assert terms.identifiers == ()

    def test_question_words_are_dropped(self) -> None:
        assert query_terms("how is the app currently storing data").terms == (
            "app",
            "currently",
            "storing",
            "data",
        )


class TestTsquery:
    def test_joins_terms_with_or(self) -> None:
        assert build_tsquery(["getdb", "get"]) == "getdb | get"

    def test_never_passes_query_syntax_through(self) -> None:
        """Only [a-z0-9]+ terms reach to_tsquery, so no operator can be injected."""
        assert build_tsquery(["getdb", "a|b", "x'y", "!z", ""]) == "getdb"


class TestTermStatistics:
    STATS = TermStatistics(
        total_chunks=50, document_frequency={"usememo": 1, "use": 10, "data": 40}
    )

    def test_rare_terms_weigh_more_than_common_ones(self) -> None:
        assert self.STATS.idf("usememo") > self.STATS.idf("use") > self.STATS.idf("data") > 0

    def test_terms_in_more_than_half_the_chunks_are_not_informative(self) -> None:
        assert self.STATS.is_informative("use")
        assert not self.STATS.is_informative("data")

    def test_coverage_is_the_matched_share_of_informative_weight(self) -> None:
        full = self.STATS.coverage(("usememo", "use", "data"), {"usememo", "use"})
        partial = self.STATS.coverage(("usememo", "use", "data"), {"use"})

        assert full == pytest.approx(1.0)  # "data" is not informative, so not required
        assert 0 < partial < 0.5

    def test_terms_absent_from_the_repository_still_count_against_coverage(self) -> None:
        """A chunk matching "processing" does not cover a question about Stripe webhooks."""
        stats = TermStatistics(total_chunks=50, document_frequency={"processing": 2})

        assert stats.coverage(("stripe", "webhook", "processing"), {"processing"}) < 0.5
