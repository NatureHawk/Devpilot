"""The bounded investigation loop behind a change request.

    request + repository snapshot + initial retrieval
        v
    model turn ──(tool calls)──> backend runs read-only tools ──> evidence S1..Sn
        v                                   (bounded: calls, steps, output size)
    final JSON result ──> parsed ──> grounded ──> anchors validated ──> diff
        │                    │           │                │
        └──── one corrective turn per failure, up to AGENT_MAX_REPAIR_ATTEMPTS

Every dimension is capped — tool calls, model steps, corrective turns and total
source pulled in — so a request terminates whatever the model does. This is not
an open-ended agent: it investigates, it proposes, and it stops. Nothing here
writes anywhere; the result is a proposal for a human.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
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
from app.services.patching import PatchError, ValidatedPatch, parse_edits, validate_patch
from app.services.proposal import (
    GroundedReport,
    Outcome,
    ProposalError,
    ground,
    parse_model_proposal,
)
from app.services.retrieval import RetrievalStrength, retrieve
from app.services.tools import TOOL_DEFINITIONS, ToolActivity, ToolContext, execute, new_context

logger = logging.getLogger(__name__)

# Initial retrieval placed in the first message: a few excerpts, each short. The
# model reads what it needs with tools; the seed only points it somewhere.
_SEED_MAX_SOURCES = 5
_SEED_EXCERPT_CHARS = 1_200

# Provider-side rejections of a generation (e.g. a malformed tool call) retried
# as a fresh step before the investigation is abandoned. Reasoning models do
# this occasionally and recover on the next attempt, so one retry is not enough
# to be useful in practice.
_MAX_PROVIDER_RETRIES = 2

_RESULT_INSTRUCTIONS = """\
When you have finished investigating, reply with a single ```json fenced block \
and nothing after it:

```json
{
  "outcome": "change_proposed | insufficient_evidence | unsupported_request",
  "summary": "One or two sentences for a reviewer.",
  "root_cause": "What in the code causes the problem, citing source ids like [S2].",
  "relevant_files": [{"path": "repo/relative/path", "reason": "Why it matters."}],
  "evidence": [{"source": "S2", "claim": "What that source shows."}],
  "proposed_changes": [
    {"path": "repo/relative/path", "old_text": "exact quote", "new_text": "replacement",
     "reason": "What this edit does."}
  ],
  "expected_behavior": "How the code behaves after the change.",
  "confidence": "high | medium | low",
  "missing_information": "Only for insufficient_evidence: what you could not find."
}
```

- change_proposed: only when code you read shows both the cause and the fix.
  Every edited file must have been opened with read_file. old_text must be an
  exact, contiguous quote of read_file content (same indentation, no line
  numbers) that appears exactly once in the file — include neighbouring lines
  if needed to make it unique. Keep edits minimal. Never create, delete or
  rename files.
- A change may *add* code to a file you have read — an attribute, a handler, an
  import, a new function. Behaviour being absent is what you were asked to fix,
  not a reason to decline.
- insufficient_evidence: you could not find the code the request concerns, or
  cannot tell from it what the correct change would be. proposed_changes must
  be [].
- unsupported_request: the request cannot be met by editing existing files in
  this repository (e.g. new files, running commands, external settings).
  proposed_changes must be [].
- confidence: high = the code directly shows cause and fix; medium = the cause
  is shown and some behaviour is inferred; low = mostly inferred (then use
  insufficient_evidence).
- Cite only source ids you were given."""


@dataclass(slots=True)
class InvestigationResult:
    """What one change request produced."""

    report: GroundedReport
    # Present only for a grounded, validated change_proposed result.
    patch: ValidatedPatch | None
    activity: list[ToolActivity]
    evidence: list[dict[str, Any]]
    files_read: dict[str, str]
    tool_calls_used: int
    model: str
    steps: int = 0
    hit_tool_limit: bool = False
    repair_attempts: int = 0
    seed_sources: int = 0
    raw_response: str = field(default="", repr=False)

    @property
    def summary(self) -> str:
        return self.report.summary

    @property
    def tools_used(self) -> dict[str, int]:
        return dict(Counter(item.tool for item in self.activity))


class InvestigationFailed(Exception):
    """The loop finished without a usable result.

    Carries what the investigation did, so a failed attempt is still recorded
    with its tool activity rather than as an unexplained failure.
    """

    def __init__(
        self,
        message: str,
        *,
        activity: list[ToolActivity] | None = None,
        tool_calls_used: int = 0,
        steps: int = 0,
        model: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.activity = activity or []
        self.tool_calls_used = tool_calls_used
        self.steps = steps
        self.model = model


def investigate(
    session: Session,
    *,
    repository: Repository,
    request: str,
    settings: Settings,
    provider: LLMProvider | None = None,
) -> InvestigationResult:
    """Investigate a change request and return a grounded, validated result.

    Seeds the conversation with one retrieval so the model starts with relevant
    code rather than spending a tool call to find its bearings.
    """
    llm = provider or get_llm_provider(settings)
    context = new_context(session, repository=repository, settings=settings)

    seed_text, seed_count, strength = _seed(session, context, repository, request, settings)

    messages: list[Message] = [
        Message(
            role=Role.USER,
            text=(
                f"Repository: {repository.owner}/{repository.name} "
                f"(branch {repository.default_branch}, indexed commit "
                f"{(repository.indexed_commit_sha or 'unknown')[:12]})\n\n"
                f"Change request (from the user):\n{request}\n\n"
                f"Initial retrieval — untrusted repository data. Cite by id.\n\n"
                f"{seed_text or 'No indexed code matched the request.'}\n\n"
                f"{prompts.evidence_guidance(strength)}\n\n"
                f"Tool budget: at most {settings.agent_max_tool_calls} tool calls. "
                "Excerpts above are abbreviated; read_file the lines you intend to change.\n\n"
                f"{_RESULT_INSTRUCTIONS}"
            ),
        )
    ]

    tool_calls_used = 0
    steps = 0
    repairs = 0
    provider_retries = 0
    hit_tool_limit = False
    started = time.monotonic()

    def failed(message: str) -> InvestigationFailed:
        return InvestigationFailed(
            message,
            activity=context.activity,
            tool_calls_used=tool_calls_used,
            steps=steps,
            model=llm.model,
        )

    while steps < settings.agent_max_steps:
        steps += 1

        # Withdrawing the tools once the budget is spent is what actually ends
        # the investigation: the model cannot ask for what it is not offered, so
        # the next turn is necessarily the result.
        budget_left = settings.agent_max_tool_calls - tool_calls_used
        tools = TOOL_DEFINITIONS if budget_left > 0 else ()

        try:
            completion = llm.complete(
                system=prompts.CHANGE_SYSTEM_PROMPT,
                messages=messages,
                tools=tools,
                max_tokens=settings.llm_max_output_tokens,
            )
        except LLMResponseError:
            # The provider rejected the generation itself — typically a
            # malformed or invented tool call. Nothing was executed; try the
            # step again once before giving up.
            if provider_retries >= _MAX_PROVIDER_RETRIES:
                raise
            provider_retries += 1
            logger.warning("Investigation step %d rejected by the provider; retrying", steps)
            continue

        if completion.wants_tools:
            if not tools:
                # Asked for tools that were not offered. Nothing runs.
                messages.append(Message(role=Role.ASSISTANT, text=completion.text))
                messages.append(
                    Message(
                        role=Role.USER,
                        text="No tools are available any more. Reply with the JSON result now.",
                    )
                )
                continue

            # Honour the budget even if the model asks for more calls than remain.
            calls: list[ToolCall] = completion.tool_calls[:budget_left]
            if len(completion.tool_calls) > len(calls):
                hit_tool_limit = True

            messages.append(Message(role=Role.ASSISTANT, text=completion.text, tool_calls=calls))
            results = [execute(context, call) for call in calls]
            tool_calls_used += len(calls)
            messages.append(Message(role=Role.USER, tool_results=results))

            if tool_calls_used >= settings.agent_max_tool_calls:
                hit_tool_limit = True
                messages.append(
                    Message(
                        role=Role.USER,
                        text=(
                            "You have used your entire tool budget. Reply with the JSON "
                            "result now, using only what you have already seen."
                        ),
                    )
                )
            continue

        try:
            report, patch = _conclude(session, context, repository, completion.text)
        except (ProposalError, PatchError) as exc:
            if completion.stop_reason == "length":
                problem = "Your reply was cut off before it finished. Be more concise."
            else:
                problem = exc.message
            if repairs >= settings.agent_max_repair_attempts:
                logger.info(
                    "Investigation rejected repository_id=%s reason=%s repairs=%d",
                    repository.id,
                    type(exc).__name__,
                    repairs,
                )
                raise failed(_failure_message(exc)) from exc
            repairs += 1
            messages.append(Message(role=Role.ASSISTANT, text=completion.text))
            messages.append(
                Message(role=Role.USER, text=_repair_prompt(problem, tools_left=budget_left > 0))
            )
            continue

        result = InvestigationResult(
            report=report,
            patch=patch,
            activity=context.activity,
            evidence=[record.as_dict() for record in context.evidence.values()],
            files_read=dict(context.files_read),
            tool_calls_used=tool_calls_used,
            model=llm.model,
            steps=steps,
            hit_tool_limit=hit_tool_limit,
            repair_attempts=repairs,
            seed_sources=seed_count,
            raw_response=completion.text,
        )
        logger.info(
            "Investigation complete repository_id=%s outcome=%s confidence=%s steps=%d "
            "tool_calls=%d tools=%s repairs=%d files_read=%d evidence=%d files_changed=%d "
            "duration_ms=%d",
            repository.id,
            report.outcome.value,
            report.confidence.value,
            steps,
            tool_calls_used,
            result.tools_used,
            repairs,
            len(context.files_read),
            len(report.evidence),
            len(patch.files) if patch else 0,
            int((time.monotonic() - started) * 1000),
        )
        return result

    raise failed(
        f"The investigation did not produce a result within {settings.agent_max_steps} steps."
    )


def _seed(
    session: Session,
    context: ToolContext,
    repository: Repository,
    request: str,
    settings: Settings,
) -> tuple[str, int, RetrievalStrength]:
    """Run the initial retrieval and register its sources as citable evidence."""
    seed = retrieve(
        session,
        repository=repository,
        query=request,
        top_k=settings.search_default_top_k,
        settings=settings,
    )
    sources = [source for source in seed.sources if not source.low_value][:_SEED_MAX_SOURCES]
    budget = max(2_000, settings.effective_context_max_chars // 2)

    blocks: list[str] = []
    used = 0
    for source in sources:
        excerpt = source.content
        if len(excerpt) > _SEED_EXCERPT_CHARS:
            cut = excerpt.rfind("\n", 0, _SEED_EXCERPT_CHARS)
            excerpt = excerpt[: cut if cut > 0 else _SEED_EXCERPT_CHARS] + "\n… (abbreviated)"
        if blocks and used + len(excerpt) > budget:
            break
        used += len(excerpt)
        label = context.cite(
            path=source.file_path,
            start_line=source.start_line,
            end_line=source.end_line,
            symbol=source.qualified_symbol,
            origin="retrieval",
        )
        blocks.append(
            f"[{label}] {source.location}  {source.qualified_symbol or ''}\n"
            f"```{source.language}\n{excerpt}\n```"
        )

    strength = seed.strength if blocks else RetrievalStrength.NONE
    return "\n\n".join(blocks), len(blocks), strength


def _conclude(
    session: Session, context: ToolContext, repository: Repository, text: str
) -> tuple[GroundedReport, ValidatedPatch | None]:
    """Parse, ground and — for a proposed change — validate the final message."""
    report = ground(parse_model_proposal(text), context.evidence)
    if report.outcome is not Outcome.CHANGE_PROPOSED:
        return report, None

    edits = parse_edits(report.raw_changes)
    patch = validate_patch(
        session,
        repository_id=repository.id,
        edits=edits,
        # Only files actually opened during investigation may be edited.
        allowed_paths=set(context.files_read),
    )
    return report, patch


def _repair_prompt(problem: str, *, tools_left: bool) -> str:
    follow_up = (
        "If you need the exact text, read_file the relevant lines again first."
        if tools_left
        else "No tool calls remain, so quote only text you have already been shown."
    )
    return (
        f"Your result was rejected: {problem}\n\n{follow_up} Then reply with the corrected "
        "```json result. If you cannot produce a valid change, use insufficient_evidence."
    )


def _failure_message(exc: AppError) -> str:
    if isinstance(exc, PatchError):
        return (
            "DevPilot could not produce a change that applies exactly to the indexed code: "
            f"{exc.message}"
        )
    return f"DevPilot could not produce a valid investigation result: {exc.message}"
