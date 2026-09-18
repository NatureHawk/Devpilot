"""Read-only investigation tools.

The registry declares what the model may ask for; the executor decides what
that actually reaches. Both live behind this package so the agent loop never
touches the database directly.
"""

from app.services.tools.executor import (
    EvidenceRecord,
    ToolActivity,
    ToolContext,
    execute,
    new_context,
    summarise_activity,
)
from app.services.tools.registry import TOOL_DEFINITIONS, TOOL_NAMES

__all__ = [
    "TOOL_DEFINITIONS",
    "TOOL_NAMES",
    "EvidenceRecord",
    "ToolActivity",
    "ToolContext",
    "execute",
    "new_context",
    "summarise_activity",
]
