"""File filtering policy and language detection."""

from __future__ import annotations

import pytest

from app.integrations.github.models import TreeEntry
from app.services.indexing.filters import (
    FileFilter,
    FilterLimits,
    SkipReason,
    decode_text,
    is_excluded_path,
    looks_binary,
)
from app.services.indexing.languages import detect_language, parseable_slugs

LIMITS = FilterLimits(max_file_bytes=1_000, max_total_bytes=10_000, max_files=5)


def blob(path: str, size: int = 100) -> TreeEntry:
    return TreeEntry(path=path, sha="sha", type="blob", size=size)


@pytest.fixture
def file_filter() -> FileFilter:
    return FileFilter(LIMITS)


class TestExcludedPaths:
    @pytest.mark.parametrize(
        "path",
        [
            ".git/config",
            "node_modules/react/index.js",
            "app/node_modules/pkg/a.js",
            ".venv/lib/mod.py",
            "venv/lib/mod.py",
            "app/__pycache__/mod.py",
            "dist/bundle.js",
            "build/out.js",
            "coverage/report.json",
            "vendor/lib.go",
            "target/debug/main.rs",
            ".next/static/x.js",
        ],
    )
    def test_generated_and_vendored_directories_are_excluded(self, path: str) -> None:
        assert is_excluded_path(path) is SkipReason.EXCLUDED_DIRECTORY

    @pytest.mark.parametrize(
        "path", ["package-lock.json", "yarn.lock", "poetry.lock", "app/go.sum", "Cargo.lock"]
    )
    def test_lockfiles_are_excluded(self, path: str) -> None:
        assert is_excluded_path(path) is SkipReason.EXCLUDED_FILE

    @pytest.mark.parametrize("path", ["app/jquery.min.js", "site.min.css", "app.bundle.js"])
    def test_build_products_are_excluded(self, path: str) -> None:
        assert is_excluded_path(path) is SkipReason.EXCLUDED_FILE

    def test_a_directory_name_used_as_a_filename_is_not_excluded(self) -> None:
        """ "build" as a segment is generated output; build.py is source."""
        assert is_excluded_path("scripts/build.py") is None
        assert is_excluded_path("dist.py") is None

    def test_exclusion_is_case_insensitive(self) -> None:
        assert is_excluded_path("Node_Modules/pkg/a.js") is SkipReason.EXCLUDED_DIRECTORY


class TestLanguageDetection:
    @pytest.mark.parametrize(
        ("path", "slug"),
        [
            ("app/main.py", "python"),
            ("types.pyi", "python"),
            ("index.js", "javascript"),
            ("component.jsx", "jsx"),
            ("service.ts", "typescript"),
            ("view.tsx", "tsx"),
            ("Main.java", "java"),
            ("main.go", "go"),
            ("query.sql", "sql"),
            ("README.md", "markdown"),
            ("config.yaml", "yaml"),
            ("package.json", "json"),
        ],
    )
    def test_extensions_resolve_to_languages(self, path: str, slug: str) -> None:
        language = detect_language(path)
        assert language is not None
        assert language.slug == slug

    @pytest.mark.parametrize("path", ["logo.png", "archive.zip", "binary.exe", "notes.docx"])
    def test_unknown_extensions_are_unsupported(self, path: str) -> None:
        assert detect_language(path) is None

    def test_known_extensionless_files_resolve(self) -> None:
        assert detect_language("Dockerfile") is not None
        assert detect_language("README") is not None

    def test_only_languages_with_a_grammar_claim_parseability(self) -> None:
        """The registry must not advertise parsing DevPilot cannot do."""
        from app.services.indexing.parser import is_parseable

        for slug in parseable_slugs():
            assert is_parseable(slug), slug

        java = detect_language("Main.java")
        assert java is not None and java.parseable is False


class TestBinaryDetection:
    def test_nul_byte_means_binary(self) -> None:
        assert looks_binary(b"\x89PNG\r\n\x1a\n\x00\x00") is True

    def test_plain_source_is_not_binary(self) -> None:
        assert looks_binary(b"def main():\n    return 1\n") is False

    def test_utf8_text_survives_decoding(self) -> None:
        assert decode_text("héllo wörld".encode()) == "héllo wörld"

    def test_undecodable_bytes_are_rejected_rather_than_mangled(self) -> None:
        # Replacing bad bytes would silently corrupt stored source and chunks.
        assert decode_text(b"\xff\xfe\x00invalid") is None


class TestFileFilterDecisions:
    def test_a_supported_source_file_is_included(self, file_filter: FileFilter) -> None:
        decision = file_filter.evaluate(blob("app/main.py", 500))
        assert decision.include is True
        assert decision.language is not None
        assert decision.language.slug == "python"

    def test_oversized_files_are_skipped(self, file_filter: FileFilter) -> None:
        decision = file_filter.evaluate(blob("app/main.py", 5_000))
        assert decision.include is False
        assert decision.reason is SkipReason.TOO_LARGE

    def test_empty_files_are_skipped(self, file_filter: FileFilter) -> None:
        assert file_filter.evaluate(blob("empty.py", 0)).reason is SkipReason.EMPTY

    def test_unsupported_extensions_are_skipped(self, file_filter: FileFilter) -> None:
        assert file_filter.evaluate(blob("logo.png", 10)).reason is SkipReason.UNSUPPORTED_TYPE

    def test_submodules_are_skipped(self, file_filter: FileFilter) -> None:
        entry = TreeEntry(path="vendored", sha="s", type="commit")
        assert file_filter.evaluate(entry).reason is SkipReason.SUBMODULE

    def test_directories_are_not_files(self, file_filter: FileFilter) -> None:
        entry = TreeEntry(path="app", sha="s", type="tree")
        assert file_filter.evaluate(entry).include is False

    def test_file_count_limit_is_enforced(self, file_filter: FileFilter) -> None:
        decision = file_filter.evaluate(blob("app/main.py", 10), accepted_files=LIMITS.max_files)
        assert decision.reason is SkipReason.FILE_LIMIT_REACHED

    def test_total_byte_budget_is_enforced(self, file_filter: FileFilter) -> None:
        decision = file_filter.evaluate(
            blob("app/main.py", 900), accepted_bytes=LIMITS.max_total_bytes - 100
        )
        assert decision.reason is SkipReason.BYTE_BUDGET_REACHED

    def test_budget_is_only_consulted_for_otherwise_acceptable_files(
        self, file_filter: FileFilter
    ) -> None:
        """An excluded path is reported as excluded, not as a budget failure."""
        decision = file_filter.evaluate(
            blob("node_modules/a.js", 900), accepted_bytes=LIMITS.max_total_bytes
        )
        assert decision.reason is SkipReason.EXCLUDED_DIRECTORY
