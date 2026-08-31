"""Tree-sitter parsing, behind an abstraction.

Nothing outside this module imports ``tree_sitter``. The chunker works with
:class:`SymbolNode` — a flat, language-neutral description of the structures a
file contains — so swapping or extending grammars never reaches the chunker.

Parsing is a pure function of bytes: no repository code is executed, and
Tree-sitter is an error-tolerant parser, so malformed source yields a partial
tree rather than an exception.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from functools import cache

from tree_sitter import Language as TSLanguage
from tree_sitter import Node, Parser

logger = logging.getLogger(__name__)


class ParserUnavailableError(Exception):
    """No grammar is configured for the requested language."""


@dataclass(frozen=True, slots=True)
class SymbolNode:
    """One meaningful structure found in a file.

    Byte offsets are authoritative — line numbers are derived from them — because
    slicing source by byte range is exact regardless of line endings.
    """

    chunk_type: str
    node_type: str
    name: str | None
    start_byte: int
    end_byte: int
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    parent_name: str | None = None


@dataclass(slots=True)
class ParsedFile:
    """The structural result of parsing one file."""

    language: str
    symbols: list[SymbolNode] = field(default_factory=list)
    # True when the grammar reported syntax errors. The symbols found are still
    # returned: a file with one broken function should not lose the other ten.
    has_errors: bool = False


# Node types that represent a definition worth isolating, per grammar.
# "method" is not a distinct node type in these grammars — a function inside a
# class body is what makes it a method, which the walk determines from context.
_PYTHON_DEFINITIONS: dict[str, str] = {
    "class_definition": "class",
    "function_definition": "function",
}

_JS_DEFINITIONS: dict[str, str] = {
    "class_declaration": "class",
    "class": "class",
    "function_declaration": "function",
    "function_expression": "function",
    "generator_function_declaration": "function",
    "method_definition": "method",
    "arrow_function": "function",
}

_TS_DEFINITIONS: dict[str, str] = {
    **_JS_DEFINITIONS,
    "interface_declaration": "class",
    "type_alias_declaration": "function",
    "enum_declaration": "class",
    "abstract_class_declaration": "class",
}

# Wrappers that belong to the declaration they contain. Without folding these
# in, "export " and a decorator line fall outside the symbol and surface as
# meaningless one-line chunks of their own.
_LEADING_WRAPPERS = frozenset({"export_statement", "decorated_definition", "ambient_declaration"})

# Binding wrappers: `const handler = () => {}` puts the useful name and keyword
# outside the function node. Folded only when the wrapper binds exactly one
# declarator — `const a = () => 1, b = () => 2` would otherwise give both
# functions the same start and produce overlapping chunks.
_BINDING_WRAPPERS = frozenset(
    {"variable_declarator", "lexical_declaration", "variable_declaration"}
)

# Node types whose presence means "the functions inside me are methods".
_CLASS_BODY_TYPES = frozenset(
    {"class_definition", "class_declaration", "class", "abstract_class_declaration"}
)

_DEFINITIONS_BY_LANGUAGE: dict[str, dict[str, str]] = {
    "python": _PYTHON_DEFINITIONS,
    "javascript": _JS_DEFINITIONS,
    "jsx": _JS_DEFINITIONS,
    "typescript": _TS_DEFINITIONS,
    "tsx": _TS_DEFINITIONS,
}

# Grammars are loaded lazily: importing every grammar at module import would
# cost startup time for a process that may never index anything.
_PARSER_LOCK = threading.Lock()


@cache
def _load_language(slug: str) -> TSLanguage:
    """Build a Tree-sitter Language, cached for the process lifetime."""
    if slug == "python":
        import tree_sitter_python

        return TSLanguage(tree_sitter_python.language())
    if slug in ("javascript", "jsx"):
        import tree_sitter_javascript

        return TSLanguage(tree_sitter_javascript.language())
    if slug == "typescript":
        import tree_sitter_typescript

        return TSLanguage(tree_sitter_typescript.language_typescript())
    if slug == "tsx":
        import tree_sitter_typescript

        return TSLanguage(tree_sitter_typescript.language_tsx())

    raise ParserUnavailableError(f"No Tree-sitter grammar configured for {slug!r}.")


def is_parseable(slug: str) -> bool:
    return slug in _DEFINITIONS_BY_LANGUAGE


def _node_name(node: Node) -> str | None:
    """Read a definition's identifier.

    Grammars expose it as a ``name`` field for declarations. Arrow functions and
    function expressions have none, so the caller supplies a name from the
    surrounding variable declarator where possible.
    """
    name_node = node.child_by_field_name("name")
    if name_node is not None and name_node.text is not None:
        return name_node.text.decode("utf-8", errors="replace")
    return None


def _named_from_declarator(node: Node) -> str | None:
    """Recover a name for an anonymous function bound to a variable.

    ``const handler = () => {}`` parses the arrow function as anonymous; the
    useful name lives on the enclosing ``variable_declarator``.
    """
    parent = node.parent
    if parent is None:
        return None
    if parent.type in ("variable_declarator", "public_field_definition", "pair"):
        return _node_name(parent) or _identifier_text(parent)
    if parent.type == "assignment_expression":
        return _identifier_text(parent)
    return None


def _identifier_text(node: Node) -> str | None:
    for child in node.children:
        if (
            child.type in ("identifier", "property_identifier", "type_identifier")
            and child.text is not None
        ):
            return child.text.decode("utf-8", errors="replace")
    return None


def _extended_start(node: Node) -> tuple[int, int]:
    """Start of a definition including any wrapper that introduces it.

    ``export class Widget`` and a decorated Python function both parse with the
    keyword or decorator outside the definition node. Chunking from the raw node
    start would leave those fragments orphaned, so the wrapper is folded in.
    """
    current = node
    while current.parent is not None and _can_fold(current.parent, current):
        current = current.parent

    return current.start_byte, current.start_point[0] + 1


def _can_fold(parent: Node, child: Node) -> bool:
    """Whether a parent node exists solely to introduce ``child``."""
    if parent.start_byte >= child.start_byte:
        return False
    if parent.type in _LEADING_WRAPPERS:
        return True
    if parent.type in _BINDING_WRAPPERS:
        declarators = [c for c in parent.named_children if c.type == "variable_declarator"]
        # A declarator itself has no declarator children; a declaration must
        # bind exactly one for the fold to stay unambiguous.
        return len(declarators) <= 1
    return False


def parse_file(source: bytes, language: str) -> ParsedFile:
    """Parse source into a flat list of structures.

    Raises :class:`ParserUnavailableError` for a language with no grammar, so a
    caller cannot mistake "not parseable" for "nothing found".
    """
    definitions = _DEFINITIONS_BY_LANGUAGE.get(language)
    if definitions is None:
        raise ParserUnavailableError(f"No Tree-sitter grammar configured for {language!r}.")

    ts_language = _load_language(language)
    # Parser objects are not documented as thread-safe; indexing parses from a
    # single thread today, but the lock keeps that from becoming a latent bug.
    with _PARSER_LOCK:
        parser = Parser(ts_language)
        tree = parser.parse(source)

    root = tree.root_node
    symbols: list[SymbolNode] = []
    _walk(root, definitions, symbols, parent_name=None, inside_class=False)
    # Source order makes downstream chunking and display deterministic.
    symbols.sort(key=lambda symbol: (symbol.start_byte, symbol.end_byte))

    return ParsedFile(language=language, symbols=symbols, has_errors=bool(root.has_error))


def _walk(
    node: Node,
    definitions: dict[str, str],
    out: list[SymbolNode],
    *,
    parent_name: str | None,
    inside_class: bool,
) -> None:
    """Depth-first walk collecting definitions and their enclosing symbol."""
    child_parent_name = parent_name
    child_inside_class = inside_class

    # is_named guards against anonymous tokens: the `class` keyword is itself a
    # node of type "class", which would otherwise match the definition map and
    # emit a duplicate, nameless symbol.
    kind = definitions.get(node.type) if node.is_named else None
    if kind is not None:
        name = _node_name(node) or _named_from_declarator(node)

        # A function directly inside a class body is a method. Nested functions
        # keep the "function" kind, which matches how a reader thinks of them.
        if kind == "function" and inside_class:
            kind = "method"

        start_byte, start_line = _extended_start(node)
        out.append(
            SymbolNode(
                chunk_type=kind,
                node_type=node.type,
                name=name,
                start_byte=start_byte,
                end_byte=node.end_byte,
                # Tree-sitter rows are 0-based; editors and GitHub are 1-based.
                start_line=start_line,
                end_line=node.end_point[0] + 1,
                parent_name=parent_name,
            )
        )

        # Children of a definition are described relative to it.
        if name is not None:
            child_parent_name = name
        child_inside_class = node.type in _CLASS_BODY_TYPES

    # Named children only: anonymous tokens carry no structure worth chunking.
    for child in node.named_children:
        _walk(
            child,
            definitions,
            out,
            parent_name=child_parent_name,
            inside_class=child_inside_class,
        )
