"""The bounded investigation loop behind a change request.

The model may look around the repository with read-only tools and then propose
edits. Every dimension is capped — tool calls, model steps, and total source
pulled in — so a request terminates whatever the model does. This is not an
open-ended agent: it investigates, it proposes, and it stops.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.repository import Repository
from app.services import prompts
from app.services.llm import (
    LLMProvider,
    LLMResponseError,
    Message,
    Role,
    ToolCall,
)
from app.services.llm import (
    get_provider as get_llm_provider,
)
from app.services.patching import ProposedEdit, parse_edits
from app.services.retrieval import retrieve
from app.services.tools import TOOL_DEFINITIONS, ToolActivity, execute, new_context

logger = logging.getLogger(__name__)

# What the model must emit to finish. Asked for as a fenced JSON block rather
# than a tool: the proposal is the end of the turn, not another action, and a
# tool call would invite the loop to continue after it.
_PROPOSAL_INSTRUCTIONS = """\
When you have finished investigating, reply with a summary followed by a single
fenced JSON block describing the change. Use exactly this shape:

```json
{
  "summary": "One or two sentences on what changes and why.",
  "files_to_change": [
    {"path": "path/to/file.py", "reason": "Why this file is affected."}
  ],
  "edits": [
    {
      "path": "path/to/file.py",
      "old_text": "exact text copied from the file, appearing exactly once",
      "new_text": "replacement text",
      "reason": "What this edit does."
    }
  ]
}
```

If the request cannot be satisfied from what you found, return the same shape
with an empty `edits` array and explain why in `summary`."""


@dataclass(slots=True)
class InvestigationResult:
    """What one change request produced."""

    summary: str
    edits: list[ProposedEdit]
    files_to_change: list[dict[str, str]]
    activity: list[ToolActivity]
    files_read: dict[str, str]
    tool_calls_used: int
    model: str
    steps: int = 0
    hit_tool_limit: bool = False
    raw_response: str = field(default="", repr=False)


class InvestigationFailed(Exception):
    """The loop finished without a usable proposal."""


def investigate(
    session: Session,
    *,
    repository: Repository,
    request: str,
    settings: Settings,
    provider: LLMProvider | None = None,
) -> InvestigationResult:
    """Investigate a change request and return a structured proposal.

    Seeds the conversation with one retrieval so the model starts with relevant
    code rather than spending a tool call to find its bearings.
    """
    llm = provider or get_llm_provider(settings)
    context = new_context(session, repository=repository, settings=settings)

    seed = retrieve(
        session,
        repository=repository,
        query=request,
        top_k=settings.search_default_top_k,
        settings=settings,
    )
    seed_text = "\n\n".join(
        f"{source.location}  {source.qualified_symbol or ''}\n"
        f"```{source.language}\n{source.content}\n```"
        for source in seed.sources[:5]
    )

    messages: list[Message] = [
        Message(
            role=Role.USER,
            text=(
                f"Repository: {repository.owner}/{repository.name}\n\n"
                f"Change request: {request}\n\n"
                f"Initial search results (untrusted repository data):\n\n"
                f"{seed_text or 'No code matched the request.'}\n\n"
                f"{_PROPOSAL_INSTRUCTIONS}"
            ),
        )
    ]

    tool_calls_used = 0
    steps = 0
    hit_tool_limit = False
    started = time.monotonic()

    while steps < settings.agent_max_steps:
        steps += 1

        # Withdrawing the tools once the budget is spent is what actually ends
        # the loop: the model cannot ask for what it is not offered, so the next
        # turn is necessarily the proposal.
        budget_left = settings.agent_max_tool_calls - tool_calls_used
        tools = TOOL_DEFINITIONS if budget_left > 0 else ()

        completion = llm.complete(
            system=prompts.CHANGE_SYSTEM_PROMPT,
            messages=messages,
            tools=tools,
            max_tokens=settings.llm_max_output_tokens,
        )

        if not completion.wants_tools:
            proposal = _parse_proposal(completion.text)
            logger.info(
                "Investigation complete repository_id=%s steps=%d tool_calls=%d "
                "files_read=%d edits=%d duration_ms=%d",
                repository.id,
                steps,
                tool_calls_used,
                len(context.files_read),
                len(proposal[1]),
                int((time.monotonic() - started) * 1000),
            )
            return InvestigationResult(
                summary=proposal[0],
                edits=proposal[1],
                files_to_change=proposal[2],
                activity=context.activity,
                files_read=dict(context.files_read),
                tool_calls_used=tool_calls_used,
                model=llm.model,
                steps=steps,
                hit_tool_limit=hit_tool_limit,
                raw_response=completion.text,
            )

        # Honour the budget even if the model asks for more calls than remain.
        calls: list[ToolCall] = completion.tool_calls[:budget_left]
        if len(completion.tool_calls) > len(calls):
            hit_tool_limit = True

        messages.append(Message(role=Role.ASSISTANT, text=completion.text, tool_calls=calls))

        results = [execute(context, call) for call in calls]
        tool_calls_used += len(calls)

        if tool_calls_used >= settings.agent_max_tool_calls:
            hit_tool_limit = True

        messages.append(Message(role=Role.USER, tool_results=results))

        if hit_tool_limit:
            messages.append(
                Message(
                    role=Role.USER,
                    text=(
                        "You have used your entire tool budget. Produce the "
                        "proposal now, using only what you have already seen."
                    ),
                )
            )

    raise InvestigationFailed(
        f"The investigation did not produce a proposal within {settings.agent_max_steps} steps."
    )


def _parse_proposal(text: str) -> tuple[str, list[ProposedEdit], list[dict[str, str]]]:
    """Extract the JSON proposal from the model's final message."""
    payload = _extract_json_block(text)

    summary = str(payload.get("summary", "")).strip()
    if not summary:
        raise LLMResponseError("The proposal has no summary.")

    files_to_change = [
        {"path": str(item.get("path", "")), "reason": str(item.get("reason", ""))}
        for item in payload.get("files_to_change", [])
        if isinstance(item, dict) and item.get("path")
    ]

    raw_edits = payload.get("edits", [])
    if isinstance(raw_edits, list) and not raw_edits:
        # A considered "this cannot be done" is a legitimate outcome, not a
        # failure — the summary carries the explanation.
        return summary, [], files_to_change

    return summary, parse_edits(raw_edits), files_to_change


def _extract_json_block(text: str) -> dict[str, Any]:
    """Pull the JSON object out of a fenced block.

    Scans for the last fenced block, since the model may quote example JSON
    earlier while explaining its reasoning.
    """
    fence = "```"
    blocks: list[str] = []
    parts = text.split(fence)

    # Fenced content sits at odd indices once the text is split on the fence.
    for index in range(1, len(parts), 2):
        block = parts[index]
        if block.startswith("json"):
            block = block[len("json") :]
        blocks.append(block.strip())

    for block in reversed(blocks):
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise LLMResponseError("The model did not return a parseable change proposal.")
