"""The investigation loop, its bounds, and patch validation.

The bounds are the security control: if the loop can be talked into running
forever, or a patch can be applied to a file the model never read, the human
review step is the only thing left standing. These tests hold those lines.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import pytest

from app.core.config import Settings
from app.models.change import ChangeStatus
from app.services.agent import InvestigationFailed, _extract_json_block, _parse_proposal
from app.services.llm import Completion, LLMResponseError, ToolCall
from app.services.patching import (
    PatchError,
    PatchMismatchError,
    ProposedEdit,
    StaleSnapshotError,
    assert_snapshot_current,
    parse_edits,
)


class ScriptedLLM:
    """Replays a fixed sequence of completions, recording each request."""

    def __init__(self, completions: Sequence[Completion]) -> None:
        self._completions = list(completions)
        self.calls = 0
        self.tools_offered: list[int] = []

    @property
    def model(self) -> str:
        return "fake-model"

    def stream(self, **_: object):  # pragma: no cover - agent uses complete()
        raise AssertionError("the investigation loop should not stream")

    def complete(self, *, system, messages, tools=(), max_tokens=None) -> Completion:
        self.tools_offered.append(len(tools))
        if self.calls >= len(self._completions):
            raise AssertionError("the loop asked for more turns than were scripted")
        completion = self._completions[self.calls]
        self.calls += 1
        return completion


def _proposal_json(path: str = "a.py", old: str = "old", new: str = "new") -> str:
    return (
        "Here is the change.\n\n```json\n"
        f'{{"summary": "Adds validation.", "files_to_change": [{{"path": "{path}", '
        f'"reason": "target"}}], "edits": [{{"path": "{path}", "old_text": "{old}", '
        f'"new_text": "{new}", "reason": "why"}}]}}'
        "\n```"
    )


class TestProposalParsing:
    def test_reads_the_json_block(self) -> None:
        summary, edits, files = _parse_proposal(_proposal_json())

        assert summary == "Adds validation."
        assert [edit.path for edit in edits] == ["a.py"]
        assert files == [{"path": "a.py", "reason": "target"}]

    def test_prefers_the_last_block_when_the_model_quotes_an_example(self) -> None:
        """Models often restate the schema before answering with it."""
        text = (
            'First, the shape:\n```json\n{"summary": "example"}\n```\n'
            "Now the real one:\n\n" + _proposal_json(path="real.py")
        )
        summary, edits, _ = _parse_proposal(text)

        assert summary == "Adds validation."
        assert edits[0].path == "real.py"

    def test_empty_edits_is_a_valid_outcome(self) -> None:
        """'I investigated and cannot do this' is an answer, not a failure."""
        text = '```json\n{"summary": "Not possible: no such endpoint.", "edits": []}\n```'
        summary, edits, _ = _parse_proposal(text)

        assert edits == []
        assert "Not possible" in summary

    def test_rejects_a_response_with_no_json(self) -> None:
        with pytest.raises(LLMResponseError):
            _parse_proposal("I had a look and things seem fine.")

    def test_rejects_a_proposal_with_no_summary(self) -> None:
        with pytest.raises(LLMResponseError):
            _parse_proposal('```json\n{"edits": []}\n```')

    def test_rejects_malformed_json(self) -> None:
        with pytest.raises(LLMResponseError):
            _extract_json_block("```json\n{not json at all}\n```")


class TestEditValidation:
    def test_rejects_an_edit_that_changes_nothing(self) -> None:
        with pytest.raises(PatchError):
            parse_edits([{"path": "a.py", "old_text": "x", "new_text": "x"}])

    def test_rejects_an_unanchored_edit(self) -> None:
        """Empty old_text has nothing to verify against, so it cannot be safe."""
        with pytest.raises(PatchError):
            parse_edits([{"path": "a.py", "old_text": "", "new_text": "y"}])

    def test_rejects_a_non_object_edit(self) -> None:
        with pytest.raises(PatchError):
            parse_edits(["just a string"])

    def test_rejects_missing_new_text(self) -> None:
        with pytest.raises(PatchError):
            parse_edits([{"path": "a.py", "old_text": "x"}])


class TestStaleSnapshot:
    def test_passes_when_the_snapshot_matches(self) -> None:
        assert_snapshot_current(proposal_sha="abc123", repository_sha="abc123")

    def test_raises_when_the_repository_moved_on(self) -> None:
        with pytest.raises(StaleSnapshotError) as exc:
            assert_snapshot_current(proposal_sha="abc123", repository_sha="def456")

        assert exc.value.code == "patch_stale"
        assert exc.value.details == {"proposed_against": "abc123", "current": "def456"}

    def test_is_permissive_when_a_sha_is_unknown(self) -> None:
        """Without both shas there is nothing to compare; do not invent staleness."""
        assert_snapshot_current(proposal_sha=None, repository_sha="abc")
        assert_snapshot_current(proposal_sha="abc", repository_sha=None)


class TestAgentBounds:
    """The loop must terminate regardless of what the model does."""

    def _settings(self, **overrides: object) -> Settings:
        return Settings(_env_file=None, ANTHROPIC_API_KEY="sk-test", **overrides)

    def test_stops_offering_tools_once_the_budget_is_spent(self, monkeypatch) -> None:
        """Withdrawing the tools is what forces the loop to conclude."""
        settings = self._settings(AGENT_MAX_TOOL_CALLS=2, AGENT_MAX_STEPS=6)

        wants_tool = Completion(
            text="",
            tool_calls=[ToolCall(id="t1", name="search_code", arguments={"query": "auth"})],
            stop_reason="tool_use",
        )
        llm = ScriptedLLM([wants_tool, wants_tool, Completion(_proposal_json(), [], "end_turn")])

        result = _run_agent(monkeypatch, llm, settings)

        assert result.tool_calls_used == 2
        # Third turn was offered no tools at all.
        assert llm.tools_offered[-1] == 0
        assert result.hit_tool_limit is True

    def test_raises_when_the_model_never_proposes(self, monkeypatch) -> None:
        """A model that only ever asks for tools must not loop forever.

        Tool budget is set high enough that the step limit is what binds, so
        this exercises the outer bound rather than the tool one.
        """
        settings = self._settings(AGENT_MAX_TOOL_CALLS=25, AGENT_MAX_STEPS=3)
        wants_tool = Completion(
            text="",
            tool_calls=[ToolCall(id="t", name="search_code", arguments={"query": "x"})],
            stop_reason="tool_use",
        )
        llm = ScriptedLLM([wants_tool] * 3)

        with pytest.raises(InvestigationFailed):
            _run_agent(monkeypatch, llm, settings)

        assert llm.calls == 3

    def test_a_direct_proposal_uses_no_tool_calls(self, monkeypatch) -> None:
        settings = self._settings()
        llm = ScriptedLLM([Completion(_proposal_json(), [], "end_turn")])

        result = _run_agent(monkeypatch, llm, settings)

        assert result.tool_calls_used == 0
        assert result.steps == 1
        assert len(result.edits) == 1


def _run_agent(monkeypatch, llm: ScriptedLLM, settings: Settings):
    """Drive the loop with retrieval and tool execution stubbed out."""
    from app.services import agent as agent_module
    from app.services.retrieval import RetrievalResult, RetrievalStrength

    monkeypatch.setattr(
        agent_module,
        "retrieve",
        lambda *a, **k: RetrievalResult(
            sources=[], strength=RetrievalStrength.NONE, model="m", searched_chunks=0
        ),
    )
    monkeypatch.setattr(
        agent_module,
        "execute",
        lambda context, call: __import__("app.services.llm", fromlist=["ToolResult"]).ToolResult(
            tool_use_id=call.id, content='{"results": []}'
        ),
    )

    class _Repo:
        id = uuid.uuid4()
        owner = "acme"
        name = "api"

    return agent_module.investigate(
        session=object(),  # type: ignore[arg-type]
        repository=_Repo(),  # type: ignore[arg-type]
        request="Add validation",
        settings=settings,
        provider=llm,  # type: ignore[arg-type]
    )


class TestChangeStatusModel:
    def test_status_values_are_stable(self) -> None:
        """These strings are persisted; renaming one is a migration."""
        assert {status.value for status in ChangeStatus} == {
            "proposed",
            "approved",
            "rejected",
            "stale",
            "failed",
        }


class TestProposedEdit:
    def test_carries_its_reason(self) -> None:
        edit = ProposedEdit(path="a.py", old_text="x", new_text="y", reason="because")
        assert edit.reason == "because"


class TestPatchMismatchIsDistinct:
    def test_mismatch_has_its_own_code(self) -> None:
        """The UI needs to tell 'model misquoted' apart from 'invalid shape'."""
        assert PatchMismatchError("x").code == "patch_mismatch"
        assert PatchError("x").code == "patch_invalid"
