"""Tree-sitter parsing and symbol extraction."""

from __future__ import annotations

import pytest

from app.services.indexing.parser import ParserUnavailableError, is_parseable, parse_file

PYTHON_SOURCE = b'''import os

CONSTANT = 1


class UserService:
    """Docstring."""

    def __init__(self, db):
        self.db = db

    def create_user(self, name):
        def validate(value):
            return bool(value)

        return validate(name)


def helper(x):
    return x * 2


@decorator
def decorated(y):
    return y
'''

TYPESCRIPT_SOURCE = b"""export class Widget {
  private id: string;

  constructor(id: string) {
    this.id = id;
  }

  render(): string {
    return this.id;
  }
}

export function build(count: number): Widget[] {
  return [];
}

export const handler = (event: string) => event.length;

interface Shape {
  area(): number;
}
"""


def symbols_by_name(source: bytes, language: str) -> dict[str, tuple[str, str | None]]:
    """name -> (chunk_type, parent_name)."""
    parsed = parse_file(source, language)
    return {s.name: (s.chunk_type, s.parent_name) for s in parsed.symbols if s.name}


class TestPython:
    def test_classes_and_functions_are_extracted(self) -> None:
        found = symbols_by_name(PYTHON_SOURCE, "python")

        assert found["UserService"][0] == "class"
        assert found["helper"][0] == "function"

    def test_functions_in_a_class_body_become_methods_with_a_parent(self) -> None:
        found = symbols_by_name(PYTHON_SOURCE, "python")

        assert found["__init__"] == ("method", "UserService")
        assert found["create_user"] == ("method", "UserService")

    def test_a_function_nested_in_a_method_stays_a_function(self) -> None:
        """Nesting inside a method does not make something a method."""
        found = symbols_by_name(PYTHON_SOURCE, "python")

        assert found["validate"] == ("function", "create_user")

    def test_a_decorator_is_part_of_the_function_it_decorates(self) -> None:
        parsed = parse_file(PYTHON_SOURCE, "python")
        decorated = next(s for s in parsed.symbols if s.name == "decorated")

        assert PYTHON_SOURCE[decorated.start_byte : decorated.end_byte].startswith(b"@decorator")

    def test_line_numbers_are_one_based(self) -> None:
        parsed = parse_file(b"def first():\n    pass\n", "python")

        assert parsed.symbols[0].start_line == 1

    def test_clean_source_reports_no_errors(self) -> None:
        assert parse_file(PYTHON_SOURCE, "python").has_errors is False


class TestTypeScript:
    def test_classes_methods_and_functions_are_extracted(self) -> None:
        found = symbols_by_name(TYPESCRIPT_SOURCE, "typescript")

        assert found["Widget"][0] == "class"
        assert found["render"] == ("method", "Widget")
        assert found["build"][0] == "function"

    def test_arrow_functions_take_the_name_they_are_bound_to(self) -> None:
        found = symbols_by_name(TYPESCRIPT_SOURCE, "typescript")

        assert found["handler"][0] == "function"

    def test_interfaces_are_captured(self) -> None:
        assert "Shape" in symbols_by_name(TYPESCRIPT_SOURCE, "typescript")

    def test_export_keyword_is_part_of_the_declaration(self) -> None:
        parsed = parse_file(TYPESCRIPT_SOURCE, "typescript")
        widget = next(s for s in parsed.symbols if s.name == "Widget")

        assert TYPESCRIPT_SOURCE[widget.start_byte : widget.end_byte].startswith(b"export class")

    def test_the_class_keyword_token_does_not_become_a_symbol(self) -> None:
        """Anonymous grammar tokens must not be mistaken for declarations."""
        parsed = parse_file(TYPESCRIPT_SOURCE, "typescript")

        assert [s for s in parsed.symbols if s.chunk_type == "class" and s.name is None] == []

    def test_tsx_components_are_parsed(self) -> None:
        source = b"export const Card = ({ title }) => <div>{title}</div>;\n"
        found = symbols_by_name(source, "tsx")

        assert "Card" in found


class TestMalformedAndUnsupported:
    def test_syntax_errors_are_reported_but_do_not_raise(self) -> None:
        parsed = parse_file(b"def broken(:\n    ???\n", "python")

        assert parsed.has_errors is True

    def test_valid_symbols_survive_alongside_a_syntax_error(self) -> None:
        """One broken function must not cost the other definitions in the file."""
        source = b"def broken(:\n  ???\n\ndef fine():\n    return 1\n\nclass Ok:\n    pass\n"
        parsed = parse_file(source, "python")
        names = {s.name for s in parsed.symbols}

        assert parsed.has_errors is True
        assert {"fine", "Ok"} <= names

    def test_empty_source_yields_no_symbols(self) -> None:
        assert parse_file(b"", "python").symbols == []

    def test_a_language_without_a_grammar_raises_rather_than_returning_nothing(self) -> None:
        """Silence would be indistinguishable from "no symbols found"."""
        with pytest.raises(ParserUnavailableError):
            parse_file(b"package main\n", "go")

    @pytest.mark.parametrize("slug", ["python", "javascript", "jsx", "typescript", "tsx"])
    def test_configured_languages_report_as_parseable(self, slug: str) -> None:
        assert is_parseable(slug) is True

    @pytest.mark.parametrize("slug", ["go", "java", "sql", "markdown", "rust"])
    def test_unconfigured_languages_report_as_unparseable(self, slug: str) -> None:
        assert is_parseable(slug) is False
