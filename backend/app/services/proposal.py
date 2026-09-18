"""The investigation result: what the model must return, and how it is checked.

The model's final message is untrusted input like any other. It is parsed from a
fenced JSON block (tool use and native structured output cannot be combined on
every provider, so the application does its own validation), validated against
a strict schema, and then *grounded*: every piece of cited evidence must name a
source the tools actually returned, and a change may only be proposed when the
evidence supports it. Anything that fails is rejected, never repaired silently.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services.llm import LLMResponseError
from app.services.tools import EvidenceRecord


class Outcome(StrEnum):
    CHANGE_PROPOSED = "change_proposed"
    # The code found does not establish the cause or the fix.
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    # Not something that can be done by editing existing indexed files.
    UNSUPPORTED_REQUEST = "unsupported_request"


class Confidence(StrEnum):
    """How directly the evidence supports the conclusion. Not a probability.

    ``high``: the code read shows both the cause and the fix. ``medium``: the
    cause is shown and some behaviour is inferred. ``low``: mostly inferred —
    never enough to propose a patch.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ProposalError(LLMResponseError):
    """The final message is not a usable investigation result.

    ``message`` is written to be shown back to the model in a corrective turn:
    it names what is wrong, never repeats repository content.
    """

    code = "proposal_invalid"


class _EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str = Field(min_length=1, max_length=12)
    claim: str = Field(min_length=1, max_length=1_000)


class _RelevantFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    path: str = Field(min_length=1, max_length=1_000)
    reason: str = Field(default="", max_length=1_000)


class ModelProposal(BaseModel):
    """The shape the model is asked for. Extra keys are ignored, not trusted."""

    model_config = ConfigDict(extra="ignore")

    outcome: Outcome
    summary: str = Field(min_length=1, max_length=2_000)
    root_cause: str = Field(default="", max_length=4_000)
    relevant_files: list[_RelevantFile] = Field(default_factory=list, max_length=20)
    evidence: list[_EvidenceItem] = Field(default_factory=list, max_length=30)
    # Kept raw: app.services.patching.parse_edits owns edit validation.
    proposed_changes: list[Any] = Field(default_factory=list, max_length=20)
    expected_behavior: str = Field(default="", max_length=2_000)
    confidence: Confidence
    missing_information: str = Field(default="", max_length=2_000)


@dataclass(slots=True)
class GroundedReport:
    """A validated result whose citations all resolve to real evidence."""

    outcome: Outcome
    summary: str
    root_cause: str
    confidence: Confidence
    expected_behavior: str
    missing_information: str
    relevant_files: list[dict[str, str]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    raw_changes: list[Any] = field(default_factory=list)
    # Citations that named no known source, dropped rather than shown.
    unresolved_citations: int = 0
    # Why a proposed change was downgraded, when it was.
    downgraded_reason: str | None = None


def parse_model_proposal(text: str) -> ModelProposal:
    """Extract and validate the JSON result from the model's final message."""
    payload = extract_json_object(text)

    # Accept the earlier field name so an older-style reply is still read
    # strictly, rather than failing on a rename.
    if "proposed_changes" not in payload and isinstance(payload.get("edits"), list):
        payload["proposed_changes"] = payload["edits"]

    try:
        return ModelProposal.model_validate(payload)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'result'}: {error['msg']}"
            for error in exc.errors()[:6]
        )
        raise ProposalError(
            f"The result JSON does not match the required shape ({problems})."
        ) from exc


def ground(proposal: ModelProposal, evidence: dict[str, EvidenceRecord]) -> GroundedReport:
    """Resolve citations and decide whether the result may carry a patch.

    Raises :class:`ProposalError` for a proposal that claims a change without
    the grounding a change requires — the model is asked to correct it. A
    low-confidence proposal is not an error: it is downgraded to insufficient
    evidence, because a speculative patch is exactly what must not be produced.
    """
    resolved: list[dict[str, Any]] = []
    unresolved = 0
    for item in proposal.evidence:
        record = evidence.get(item.source.strip().strip("[]"))
        if record is None:
            unresolved += 1
            continue
        resolved.append({**record.as_dict(), "claim": item.claim.strip()})

    known_paths = {record.path for record in evidence.values()}
    relevant_files = [
        {"path": item.path.strip(), "reason": item.reason.strip()}
        for item in proposal.relevant_files
        if item.path.strip() in known_paths
    ]

    report = GroundedReport(
        outcome=proposal.outcome,
        summary=proposal.summary.strip(),
        root_cause=proposal.root_cause.strip(),
        confidence=proposal.confidence,
        expected_behavior=proposal.expected_behavior.strip(),
        missing_information=proposal.missing_information.strip(),
        relevant_files=relevant_files,
        evidence=resolved,
        unresolved_citations=unresolved,
    )

    if proposal.outcome is not Outcome.CHANGE_PROPOSED:
        # Whatever edits came along with a "cannot do this" are discarded: a
        # patch is only ever produced from a positive, grounded conclusion.
        return report

    if not proposal.proposed_changes:
        raise ProposalError(
            "outcome is change_proposed but proposed_changes is empty. Either provide the "
            "edits or use insufficient_evidence."
        )
    if not report.root_cause:
        raise ProposalError("A proposed change must state the root_cause, citing sources.")
    if not resolved:
        raise ProposalError(
            "A proposed change must cite evidence using the source ids you were given "
            "(for example S1). None of the cited ids exist."
        )

    if proposal.confidence is Confidence.LOW:
        report.outcome = Outcome.INSUFFICIENT_EVIDENCE
        report.downgraded_reason = (
            "The investigation's own confidence was low, so no patch was generated."
        )
        return report

    report.raw_changes = list(proposal.proposed_changes)
    return report


def extract_json_object(text: str) -> dict[str, Any]:
    """Pull the result object out of the model's reply.

    Prefers the last fenced block, since a model may quote example JSON earlier
    while explaining itself. Falls back to the last top-level object in the text
    for a reply that forgot the fence. Code inside JSON strings can itself
    contain backtick fences, so blocks are decoded with a JSON decoder rather
    than by splitting on fences.
    """
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []

    search_from = 0
    while True:
        fence = text.find("```", search_from)
        if fence == -1:
            break
        body_start = text.find("\n", fence)
        if body_start == -1:
            break
        brace = _skip_whitespace(text, body_start)
        if brace < len(text) and text[brace] == "{":
            try:
                parsed, end = decoder.raw_decode(text, brace)
            except json.JSONDecodeError:
                search_from = body_start
                continue
            if isinstance(parsed, dict):
                candidates.append(parsed)
            closing = text.find("```", end)
            search_from = closing + 3 if closing != -1 else end
        else:
            search_from = body_start

    if not candidates:
        position = 0
        while True:
            brace = text.find("{", position)
            if brace == -1:
                break
            try:
                parsed, end = decoder.raw_decode(text, brace)
            except json.JSONDecodeError:
                position = brace + 1
                continue
            if isinstance(parsed, dict):
                candidates.append(parsed)
            position = end

    for candidate in reversed(candidates):
        if "outcome" in candidate or "summary" in candidate:
            return candidate

    raise ProposalError(
        "The reply did not contain the result as a JSON object in a ```json fenced block."
    )


def _skip_whitespace(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index
