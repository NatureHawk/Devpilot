"""Language detection.

One registry maps an extension to a language, and records whether a Tree-sitter
grammar is actually configured for it. ``parseable`` is the honest answer to
"can DevPilot understand this file's structure?" — a language is listed as
parseable only when :mod:`app.services.indexing.parser` can really parse it, so
the UI never claims syntax awareness it does not have.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True, slots=True)
class Language:
    slug: str
    label: str
    extensions: tuple[str, ...]
    content_type: str
    # True only where a grammar is wired up in the parser registry.
    parseable: bool = False


# Parsed languages. Kept deliberately small: each one needs a grammar, node-type
# mappings and tests, and a half-configured grammar is worse than none.
_PARSED: tuple[Language, ...] = (
    Language("python", "Python", (".py", ".pyi"), "text/x-python", parseable=True),
    Language(
        "javascript", "JavaScript", (".js", ".mjs", ".cjs"), "text/javascript", parseable=True
    ),
    Language("jsx", "JavaScript (JSX)", (".jsx",), "text/jsx", parseable=True),
    Language(
        "typescript", "TypeScript", (".ts", ".mts", ".cts"), "text/typescript", parseable=True
    ),
    Language("tsx", "TypeScript (TSX)", (".tsx",), "text/tsx", parseable=True),
)

# Stored and searchable as text, but not structurally parsed. Listing them is
# still useful: the file content is indexed and the language is displayed.
_STORED_ONLY: tuple[Language, ...] = (
    Language("java", "Java", (".java",), "text/x-java-source"),
    Language("c", "C", (".c", ".h"), "text/x-c"),
    Language("cpp", "C++", (".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"), "text/x-c++"),
    Language("csharp", "C#", (".cs",), "text/x-csharp"),
    Language("go", "Go", (".go",), "text/x-go"),
    Language("rust", "Rust", (".rs",), "text/x-rust"),
    Language("ruby", "Ruby", (".rb",), "text/x-ruby"),
    Language("php", "PHP", (".php",), "text/x-php"),
    Language("sql", "SQL", (".sql",), "text/x-sql"),
    Language("markdown", "Markdown", (".md", ".markdown"), "text/markdown"),
    Language("json", "JSON", (".json",), "application/json"),
    Language("yaml", "YAML", (".yml", ".yaml"), "application/yaml"),
    Language("toml", "TOML", (".toml",), "application/toml"),
    Language("shell", "Shell", (".sh", ".bash"), "text/x-shellscript"),
    Language("html", "HTML", (".html", ".htm"), "text/html"),
    Language("css", "CSS", (".css", ".scss"), "text/css"),
    Language("text", "Text", (".txt", ".rst", ".cfg", ".ini", ".env"), "text/plain"),
)

LANGUAGES: tuple[Language, ...] = _PARSED + _STORED_ONLY

_BY_EXTENSION: dict[str, Language] = {
    extension: language for language in LANGUAGES for extension in language.extensions
}
_BY_SLUG: dict[str, Language] = {language.slug: language for language in LANGUAGES}

# Files that carry meaning but have no extension.
_BY_FILENAME: dict[str, Language] = {
    "dockerfile": _BY_SLUG["text"],
    "makefile": _BY_SLUG["text"],
    "readme": _BY_SLUG["markdown"],
    "license": _BY_SLUG["text"],
}


def detect_language(path: str) -> Language | None:
    """Identify a path's language, or None if the extension is not supported.

    Matching is case-insensitive because repositories contain ``.PY`` and
    ``README`` as readily as their lowercase forms.
    """
    name = PurePosixPath(path).name.lower()

    # Longest-suffix first, so ".d.ts" style compound extensions still resolve
    # via their final component and ".tar.gz" never looks like ".gz" alone here.
    suffix = PurePosixPath(name).suffix
    if suffix and suffix in _BY_EXTENSION:
        return _BY_EXTENSION[suffix]

    if not suffix and name in _BY_FILENAME:
        return _BY_FILENAME[name]

    return None


def get_language(slug: str) -> Language | None:
    return _BY_SLUG.get(slug)


def parseable_slugs() -> tuple[str, ...]:
    return tuple(language.slug for language in LANGUAGES if language.parseable)


def supported_extensions() -> tuple[str, ...]:
    return tuple(sorted(_BY_EXTENSION))
