"""The tools the investigation loop may use.

Three read-only tools, deliberately. Each reads the indexed snapshot already in
PostgreSQL — none re-parses the repository, calls GitHub, touches the host
filesystem, or executes anything. There is no write tool and no shell tool: the
model proposes edits as structured data that a human approves, and cannot reach
a repository at all.
"""

from __future__ import annotations

from app.services.llm import ToolDefinition

SEARCH_CODE = "search_code"
READ_FILE = "read_file"
FIND_SYMBOL = "find_symbol"

# Bounds applied to every tool result, so no single call can flood the context.
MAX_SEARCH_RESULTS = 10
MAX_READ_LINES = 400
MAX_SYMBOL_RESULTS = 10

TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name=SEARCH_CODE,
        description=(
            "Search this repository's indexed code by meaning. Use it to find code "
            "related to a concept when you do not know the file or symbol name. "
            "Returns ranked excerpts with file paths and line ranges."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to look for, in natural language.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Maximum results (1-{MAX_SEARCH_RESULTS}). Defaults to 5.",
                    "minimum": 1,
                    "maximum": MAX_SEARCH_RESULTS,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name=READ_FILE,
        description=(
            "Read lines from one indexed file in this repository. Use it after "
            "search to see the full context around code you intend to change. "
            "Paths are repository-relative, exactly as returned by other tools."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Repository-relative path, e.g. backend/app/main.py.",
                },
                "start_line": {
                    "type": "integer",
                    "description": "First line to read, 1-based. Defaults to 1.",
                    "minimum": 1,
                },
                "end_line": {
                    "type": "integer",
                    "description": (
                        f"Last line to read, inclusive. At most {MAX_READ_LINES} "
                        "lines are returned per call."
                    ),
                    "minimum": 1,
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name=FIND_SYMBOL,
        description=(
            "Find a named function, class or method in this repository by exact or "
            "partial name. Use it when you know what something is called. Returns "
            "the definition's location and source."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Symbol name, e.g. create_session or AuthService.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Maximum results (1-{MAX_SYMBOL_RESULTS}). Defaults to 5.",
                    "minimum": 1,
                    "maximum": MAX_SYMBOL_RESULTS,
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    ),
)

TOOL_NAMES = frozenset(tool.name for tool in TOOL_DEFINITIONS)
