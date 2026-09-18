"""The investigation loop, its bounds, result grounding, and patch validation.

The bounds are the security control: if the loop can be talked into running
forever, a tool can reach outside the repository, a result can cite evidence
that does not exist, or a patch can apply somewhere other than exactly where it
was quoted, the human review step is the only thing left standing. These tests
hold those lines without a database or a network.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from typing import Any

import pytest

from app.core.config import Settings
from app.models.change import ChangeStatus
from app.services.agent import InvestigationFailed
from app.services.llm import Completion, LLMResponseError, ToolCall, ToolResult
from app.services.patching import (
    FilePatch,
    PatchError,
    PatchMismatchError,
    PatchScopeError,
    ProposedEdit,
    StaleSnapshotError,
    ValidatedPatch,
    _apply_anchored_edits,
    _assert_consistent,
    _diff_lines,
    apply_unified_diff,
    assert_snapshot_current,
    parse_edits,
    require_repository_path,
)
from app.services.proposal import (
    Confidence,
    Outcome,
    ProposalError,
    extract_json_object,
    ground,
    parse_model_proposal,
)
from app.services.tools import EvidenceRecord, ToolContext, execute


class ScriptedLLM:
    """Replays a fixed sequence of completions (or errors), recording each request."""

    def __init__(self, completions: Sequence[Completion | Exception]) -> None:
        self._completions = list(completions)
        self.calls = 0
        self.tools_offered: list[int] = []
        self.last_messages: list[Any] = []

    @property
    def model(self) -> str:
        return "fake-model"

    def stream(self, **_: object):  # pragma: no cover - agent uses complete()
        raise AssertionError("the investigation loop should not stream")

    def complete(self, *, system, messages, tools=(), max_tokens=None) -> Completion:
        self.tools_offered.append(len(tools))
        self.last_messages = list(messages)
        if self.calls >= len(self._completions):
            raise AssertionError("the loop asked for more turns than were scripted")
        completion = self._completions[self.calls]
        self.calls += 1
        if isinstance(completion, Exception):
            raise completion
        return completion


def _result(
    outcome: str = "change_proposed",
    *,
    path: str = "a.py",
    old: str = "old",
    new: str = "new",
    sources: Sequence[str] = ("S1",),
    confidence: str = "high",
    changes: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    proposed = (
        changes
        if changes is not None
        else (
            [{"path": path, "old_text": old, "new_text": new, "reason": "why"}]
            if outcome == "change_proposed"
            else []
        )
    )
    return {
        "outcome": outcome,
        "summary": "Adds validation.",
        "root_cause": "The handler never checks the payload [S1].",
        "relevant_files": [{"path": path, "reason": "target"}],
        "evidence": [{"source": label, "claim": "shows the handler"} for label in sources],
        "proposed_changes": proposed,
        "expected_behavior": "Invalid payloads are rejected.",
        "confidence": confidence,
    }


def _fenced(payload: dict[str, Any]) -> str:
    return f"Here is the result.\n\n```json\n{json.dumps(payload)}\n```"


def _final(payload: dict[str, Any]) -> Completion:
    return Completion(_fenced(payload), [], "stop")


def _evidence(path: str = "a.py") -> dict[str, EvidenceRecord]:
    return {
        "S1": EvidenceRecord(
            id="S1", path=path, start_line=1, end_line=5, symbol="handler", origin="read_file"
        )
    }


# ---- result parsing ----------------------------------------------------------


class TestResultParsing:
    def test_reads_the_json_block(self) -> None:
        proposal = parse_model_proposal(_fenced(_result()))

        assert proposal.outcome is Outcome.CHANGE_PROPOSED
        assert proposal.confidence is Confidence.HIGH
        assert proposal.proposed_changes[0]["path"] == "a.py"

    def test_prefers_the_last_block_when_the_model_quotes_an_example(self) -> None:
        """Models often restate the schema before answering with it."""
        text = (
            'First, the shape:\n```json\n{"summary": "example"}\n```\n'
            "Now the real one:\n\n" + _fenced(_result(path="real.py"))
        )
        assert parse_model_proposal(text).proposed_changes[0]["path"] == "real.py"

    def test_code_fences_inside_json_strings_do_not_break_extraction(self) -> None:
        payload = _result(new="```js\nconst x = 1;\n```")
        assert (
            parse_model_proposal(_fenced(payload))
            .proposed_changes[0]["new_text"]
            .startswith("```js")
        )

    def test_accepts_an_unfenced_object(self) -> None:
        text = "Result: " + json.dumps(_result())
        assert parse_model_proposal(text).summary == "Adds validation."

    def test_rejects_a_response_with_no_json(self) -> None:
        with pytest.raises(ProposalError):
            parse_model_proposal("I had a look and things seem fine.")

    def test_rejects_malformed_json(self) -> None:
        with pytest.raises(ProposalError):
            extract_json_object("```json\n{not json at all}\n```")

    def test_rejects_an_unknown_outcome(self) -> None:
        payload = _result()
        payload["outcome"] = "just_do_it"
        with pytest.raises(ProposalError) as exc:
            parse_model_proposal(_fenced(payload))
        assert "outcome" in exc.value.message

    def test_rejects_a_numeric_confidence(self) -> None:
        """Confidence is qualitative. A probability is not accepted."""
        payload = _result()
        payload["confidence"] = 0.87
        with pytest.raises(ProposalError):
            parse_model_proposal(_fenced(payload))

    def test_rejects_a_result_with_no_summary(self) -> None:
        payload = _result()
        del payload["summary"]
        with pytest.raises(ProposalError):
            parse_model_proposal(_fenced(payload))


# ---- grounding -----------------------------------------------------------------


class TestGrounding:
    def test_citations_resolve_to_locations(self) -> None:
        report = ground(parse_model_proposal(_fenced(_result())), _evidence())

        assert report.outcome is Outcome.CHANGE_PROPOSED
        assert report.evidence[0]["path"] == "a.py"
        assert report.evidence[0]["start_line"] == 1
        assert report.evidence[0]["claim"] == "shows the handler"

    def test_invented_citations_are_dropped_and_counted(self) -> None:
        payload = _result(sources=("S1", "S99"))
        report = ground(parse_model_proposal(_fenced(payload)), _evidence())

        assert [item["id"] for item in report.evidence] == ["S1"]
        assert report.unresolved_citations == 1

    def test_a_change_citing_no_real_evidence_is_rejected(self) -> None:
        payload = _result(sources=("S42",))
        with pytest.raises(ProposalError):
            ground(parse_model_proposal(_fenced(payload)), _evidence())

    def test_change_proposed_without_changes_is_rejected(self) -> None:
        payload = _result(changes=[])
        with pytest.raises(ProposalError):
            ground(parse_model_proposal(_fenced(payload)), _evidence())

    def test_low_confidence_is_downgraded_to_insufficient_evidence(self) -> None:
        """A speculative patch is never produced, however it is labelled."""
        payload = _result(confidence="low")
        report = ground(parse_model_proposal(_fenced(payload)), _evidence())

        assert report.outcome is Outcome.INSUFFICIENT_EVIDENCE
        assert report.raw_changes == []
        assert report.downgraded_reason

    def test_edits_attached_to_insufficient_evidence_are_discarded(self) -> None:
        payload = _result(
            "insufficient_evidence",
            changes=[{"path": "a.py", "old_text": "x", "new_text": "y"}],
        )
        report = ground(parse_model_proposal(_fenced(payload)), _evidence())

        assert report.outcome is Outcome.INSUFFICIENT_EVIDENCE
        assert report.raw_changes == []

    def test_relevant_files_never_seen_are_dropped(self) -> None:
        payload = _result()
        payload["relevant_files"].append({"path": "invented/module.py", "reason": "guess"})
        report = ground(parse_model_proposal(_fenced(payload)), _evidence())

        assert [item["path"] for item in report.relevant_files] == ["a.py"]


# ---- edits and paths -------------------------------------------------------------


class TestEditValidation:
    def test_rejects_an_edit_that_changes_nothing(self) -> None:
        with pytest.raises(PatchError):
            parse_edits([{"path": "a.py", "old_text": "x", "new_text": "x"}])

    def test_rejects_an_unanchored_edit(self) -> None:
        """Empty old_text has nothing to verify against, so it cannot be safe."""
        with pytest.raises(PatchError):
            parse_edits([{"path": "a.py", "old_text": "  \n", "new_text": "y"}])

    def test_rejects_a_non_object_edit(self) -> None:
        with pytest.raises(PatchError):
            parse_edits(["just a string"])

    def test_rejects_missing_new_text(self) -> None:
        with pytest.raises(PatchError):
            parse_edits([{"path": "a.py", "old_text": "x"}])

    def test_rejects_too_many_edits(self) -> None:
        edits = [{"path": "a.py", "old_text": f"x{i}", "new_text": "y"} for i in range(21)]
        with pytest.raises(PatchError):
            parse_edits(edits)

    @pytest.mark.parametrize(
        "path",
        [
            "../secrets.py",
            "src/../../etc/passwd",
            "/etc/passwd",
            "C:\\Windows\\system.ini",
            "C:/Windows/system.ini",
            "src\\app.py",
            "src//app.py",
            "./app.py",
            "app.py\x00.txt",
        ],
    )
    def test_paths_outside_the_repository_are_out_of_scope(self, path: str) -> None:
        with pytest.raises(PatchScopeError):
            require_repository_path(path)
        with pytest.raises(PatchScopeError):
            parse_edits([{"path": path, "old_text": "x", "new_text": "y"}])

    def test_a_plain_relative_path_is_accepted(self) -> None:
        assert require_repository_path(" frontend/src/App.jsx ") == "frontend/src/App.jsx"


class TestAnchors:
    ORIGINAL = "def a():\n    return 1\n\ndef b():\n    return 1\n"

    def test_missing_anchor_is_rejected(self) -> None:
        with pytest.raises(PatchMismatchError) as exc:
            _apply_anchored_edits("m.py", self.ORIGINAL, [ProposedEdit("m.py", "return 3", "x")])
        assert exc.value.details["occurrences"] == 0

    def test_duplicate_anchor_is_rejected(self) -> None:
        with pytest.raises(PatchMismatchError) as exc:
            _apply_anchored_edits(
                "m.py", self.ORIGINAL, [ProposedEdit("m.py", "    return 1", "    return 2")]
            )
        assert exc.value.details["occurrences"] == 2

    def test_a_longer_quote_disambiguates(self) -> None:
        updated, anchors = _apply_anchored_edits(
            "m.py",
            self.ORIGINAL,
            [ProposedEdit("m.py", "def b():\n    return 1", "def b():\n    return 2")],
        )
        assert updated.endswith("def b():\n    return 2\n")
        assert (anchors[0].start_line, anchors[0].end_line) == (4, 5)

    def test_overlapping_anchors_are_rejected(self) -> None:
        with pytest.raises(PatchMismatchError):
            _apply_anchored_edits(
                "m.py",
                self.ORIGINAL,
                [
                    ProposedEdit("m.py", "def a():\n    return 1", "x"),
                    ProposedEdit("m.py", "return 1\n\ndef b", "y"),
                ],
            )

    def test_an_anchor_cannot_match_text_another_edit_inserted(self) -> None:
        """Anchors are located in the original file, never in a partial result."""
        with pytest.raises(PatchMismatchError):
            _apply_anchored_edits(
                "m.py",
                self.ORIGINAL,
                [
                    ProposedEdit("m.py", "def a():", "def a():  # MARKER"),
                    ProposedEdit("m.py", "# MARKER", "# changed"),
                ],
            )

    def test_result_does_not_depend_on_edit_order(self) -> None:
        first = ProposedEdit("m.py", "def a():", "def alpha():")
        second = ProposedEdit("m.py", "def b():", "def beta():")
        forward, _ = _apply_anchored_edits("m.py", self.ORIGINAL, [first, second])
        backward, _ = _apply_anchored_edits("m.py", self.ORIGINAL, [second, first])
        assert forward == backward

    def test_crlf_files_are_matched_and_keep_their_line_endings(self) -> None:
        original = "one\r\ntwo\r\nthree\r\n"
        updated, _ = _apply_anchored_edits(
            "w.txt", original, [ProposedEdit("w.txt", "two\nthree", "2\n3")]
        )
        assert updated == "one\r\n2\r\n3\r\n"


class TestDiffConsistency:
    def test_generated_diff_reapplies_to_the_patched_file(self) -> None:
        original = "\n".join(f"line {i}" for i in range(40)) + "\n"
        updated = original.replace("line 3\n", "line three\n").replace("line 30\n", "")
        _assert_consistent("f.txt", original, updated)

    def test_a_tampered_diff_does_not_apply(self) -> None:
        original = "a\nb\nc\n"
        diff = _diff_lines(original, "a\nB\nc\n", "f.txt")
        tampered = [line.replace(" a", " z") if line == " a" else line for line in diff]
        with pytest.raises(PatchError):
            apply_unified_diff(original.splitlines(), tampered)

    def test_no_op_edits_are_refused(self) -> None:
        with pytest.raises(PatchError):
            _assert_consistent("f.txt", "same\n", "same\n")

    def test_diff_is_rendered_from_content_not_supplied(self) -> None:
        patch = ValidatedPatch(files=[FilePatch(path="f.txt", original="a\n", updated="b\n")])
        assert patch.diff.splitlines()[:2] == ["--- a/f.txt", "+++ b/f.txt"]
        assert patch.files[0].additions == 1 and patch.files[0].deletions == 1


class TestStaleSnapshot:
    def test_passes_when_the_snapshot_matches(self) -> None:
        assert_snapshot_current(proposal_sha="abc123", repository_sha="abc123")

    def test_raises_when_the_repository_moved_on(self) -> None:
        with pytest.raises(StaleSnapshotError) as exc:
            assert_snapshot_current(proposal_sha="abc123", repository_sha="def456")

        assert exc.value.code == "patch_stale"
        assert "refresh the investigation" in exc.value.message.lower()
        assert exc.value.details == {"proposed_against": "abc123", "current": "def456"}

    def test_is_permissive_when_a_sha_is_unknown(self) -> None:
        """Without both shas there is nothing to compare; do not invent staleness."""
        assert_snapshot_current(proposal_sha=None, repository_sha="abc")
        assert_snapshot_current(proposal_sha="abc", repository_sha=None)


# ---- tool executor -----------------------------------------------------------------


def _tool_context(settings: Settings | None = None) -> ToolContext:
    class _Repo:
        id = uuid.uuid4()

    class _NoDatabase:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"the tool must not reach the database ({name})")

    return ToolContext(
        session=_NoDatabase(),  # type: ignore[arg-type]
        repository=_Repo(),  # type: ignore[arg-type]
        settings=settings or Settings(_env_file=None, LLM_PROVIDER="groq", GROQ_API_KEY="k"),
    )


class TestToolExecutor:
    def test_an_invalid_tool_is_an_error_result_not_an_exception(self) -> None:
        context = _tool_context()
        result = execute(context, ToolCall(id="t1", name="run_shell", arguments={"cmd": "ls"}))

        assert result.is_error
        assert "Unknown tool" in json.loads(result.content)["error"]
        assert context.activity[0].ok is False

    @pytest.mark.parametrize("path", ["../../etc/passwd", "/etc/passwd", "C:\\Windows\\win.ini"])
    def test_read_file_outside_the_repository_is_refused_before_any_lookup(self, path: str) -> None:
        context = _tool_context()
        result = execute(context, ToolCall(id="t1", name="read_file", arguments={"path": path}))

        assert result.is_error
        assert "outside the repository scope" in json.loads(result.content)["error"]
        assert context.files_read == {}

    def test_environment_files_are_not_readable(self) -> None:
        context = _tool_context()
        result = execute(context, ToolCall(id="t1", name="read_file", arguments={"path": ".env"}))

        assert result.is_error
        assert context.files_read == {}

    def test_malformed_arguments_are_an_error_result(self) -> None:
        context = _tool_context()
        result = execute(context, ToolCall(id="t1", name="read_file", arguments={"path": 42}))

        assert result.is_error
        assert "Invalid arguments" in json.loads(result.content)["error"]

    def test_exhausted_output_budget_refuses_further_calls(self) -> None:
        context = _tool_context()
        context.output_chars_used = context.settings.effective_agent_tool_output_chars
        result = execute(context, ToolCall(id="t1", name="find_symbol", arguments={"name": "x"}))

        assert result.is_error
        assert "budget" in json.loads(result.content)["error"]

    def test_groq_budgets_fit_its_token_per_minute_limit(self) -> None:
        settings = Settings(_env_file=None, LLM_PROVIDER="groq", GROQ_API_KEY="k")
        assert settings.effective_agent_tool_output_chars <= 16_000
        assert settings.effective_agent_tool_result_chars <= 8_000

    def test_repeated_citation_of_the_same_span_keeps_its_label(self) -> None:
        context = _tool_context()
        first = context.cite(path="a.py", start_line=1, end_line=9, symbol=None, origin="read_file")
        second = context.cite(
            path="a.py", start_line=1, end_line=9, symbol="f", origin="find_symbol"
        )
        third = context.cite(
            path="a.py", start_line=10, end_line=20, symbol=None, origin="read_file"
        )
        assert (first, second, third) == ("S1", "S1", "S2")

    def test_unread_ranges_report_every_gap(self) -> None:
        """Models paginate by hand and skip a range, then call the code missing."""
        context = _tool_context()
        context.record_read("a.py", 1, 120)
        assert context.unread_ranges("a.py", 298) == [(121, 298)]

        context.record_read("a.py", 150, 240)
        context.record_read("a.py", 240, 298)
        assert context.unread_ranges("a.py", 298) == [(121, 149)]

        context.record_read("a.py", 121, 149)
        assert context.unread_ranges("a.py", 298) == []

    def test_overlapping_and_unordered_reads_do_not_invent_gaps(self) -> None:
        context = _tool_context()
        context.record_read("a.py", 40, 80)
        context.record_read("a.py", 1, 60)
        assert context.unread_ranges("a.py", 80) == []


# ---- the loop ----------------------------------------------------------------------


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, LLM_PROVIDER="groq", GROQ_API_KEY="k", **overrides)


def _wants(name: str = "search_code", **arguments: object) -> Completion:
    return Completion(
        text="",
        tool_calls=[
            ToolCall(id=f"t-{name}", name=name, arguments=dict(arguments or {"query": "x"}))
        ],
        stop_reason="tool_calls",
    )


class TestAgentLoop:
    """The loop must terminate, and fail closed, regardless of what the model does."""

    def test_stops_offering_tools_once_the_budget_is_spent(self, monkeypatch) -> None:
        """Withdrawing the tools is what forces the loop to conclude."""
        llm = ScriptedLLM([_wants(), _wants(), _final(_result())])
        result = _run_agent(monkeypatch, llm, _settings(AGENT_MAX_TOOL_CALLS=2, AGENT_MAX_STEPS=6))

        assert result.tool_calls_used == 2
        assert llm.tools_offered[-1] == 0
        assert result.hit_tool_limit is True
        assert result.patch is not None

    def test_calls_beyond_the_budget_in_one_turn_are_never_run(self, monkeypatch) -> None:
        executed: list[str] = []
        many = Completion(
            text="",
            tool_calls=[
                ToolCall(id=f"t{i}", name="search_code", arguments={"query": str(i)})
                for i in range(5)
            ],
            stop_reason="tool_calls",
        )
        llm = ScriptedLLM([many, _final(_result())])
        result = _run_agent(monkeypatch, llm, _settings(AGENT_MAX_TOOL_CALLS=3), executed=executed)

        assert executed == ["t0", "t1", "t2"]
        assert result.tool_calls_used == 3
        assert result.hit_tool_limit

    def test_raises_when_the_model_never_concludes(self, monkeypatch) -> None:
        """A model that only ever asks for tools must not loop forever."""
        llm = ScriptedLLM([_wants()] * 3)

        with pytest.raises(InvestigationFailed) as exc:
            _run_agent(monkeypatch, llm, _settings(AGENT_MAX_TOOL_CALLS=25, AGENT_MAX_STEPS=3))

        assert llm.calls == 3
        assert exc.value.tool_calls_used == 3

    def test_malformed_result_gets_one_corrective_turn(self, monkeypatch) -> None:
        llm = ScriptedLLM([Completion("Looks fine to me.", [], "stop"), _final(_result())])
        result = _run_agent(monkeypatch, llm, _settings())

        assert result.repair_attempts == 1
        assert result.patch is not None
        # The correction was sent as a user turn naming the problem.
        assert "rejected" in llm.last_messages[-1].text

    def test_persistently_malformed_results_fail_closed(self, monkeypatch) -> None:
        llm = ScriptedLLM([Completion("no json", [], "stop")] * 3)

        with pytest.raises(InvestigationFailed) as exc:
            _run_agent(monkeypatch, llm, _settings(AGENT_MAX_REPAIR_ATTEMPTS=2))

        assert llm.calls == 3
        assert "valid investigation result" in exc.value.message

    def test_anchor_mismatch_is_fed_back_then_fails_closed(self, monkeypatch) -> None:
        def reject(*_: object, **__: object) -> ValidatedPatch:
            raise PatchMismatchError(
                "The quoted text for 'a.py' does not appear in the indexed file."
            )

        llm = ScriptedLLM([_final(_result())] * 2)
        with pytest.raises(InvestigationFailed) as exc:
            _run_agent(monkeypatch, llm, _settings(AGENT_MAX_REPAIR_ATTEMPTS=1), validate=reject)

        assert "does not appear" in exc.value.message
        assert "does not appear" in llm.last_messages[-1].text

    def test_insufficient_evidence_produces_no_patch(self, monkeypatch) -> None:
        def never(*_: object, **__: object) -> ValidatedPatch:
            raise AssertionError("no patch may be validated without a proposed change")

        payload = _result("insufficient_evidence")
        payload["missing_information"] = "No product list component was found."
        llm = ScriptedLLM([_final(payload)])
        result = _run_agent(monkeypatch, llm, _settings(), validate=never)

        assert result.patch is None
        assert result.report.outcome is Outcome.INSUFFICIENT_EVIDENCE
        assert result.report.missing_information

    def test_unsupported_request_produces_no_patch(self, monkeypatch) -> None:
        llm = ScriptedLLM([_final(_result("unsupported_request"))])
        result = _run_agent(monkeypatch, llm, _settings())

        assert result.patch is None
        assert result.report.outcome is Outcome.UNSUPPORTED_REQUEST

    def test_provider_rejected_generation_is_retried_once(self, monkeypatch) -> None:
        llm = ScriptedLLM([LLMResponseError("tool_use_failed"), _final(_result())])
        result = _run_agent(monkeypatch, llm, _settings())

        assert result.patch is not None
        assert llm.calls == 2

    def test_provider_rejections_beyond_the_retry_budget_propagate(self, monkeypatch) -> None:
        llm = ScriptedLLM([LLMResponseError(str(i)) for i in range(3)])
        with pytest.raises(LLMResponseError):
            _run_agent(monkeypatch, llm, _settings())

    def test_tool_calls_when_no_tools_are_offered_are_not_run(self, monkeypatch) -> None:
        executed: list[str] = []
        llm = ScriptedLLM([_wants(), _wants(), _final(_result())])
        result = _run_agent(monkeypatch, llm, _settings(AGENT_MAX_TOOL_CALLS=1), executed=executed)

        assert len(executed) == 1
        assert result.patch is not None


class TestProviderFailures:
    """Faults from the provider itself, rather than from what it generated."""

    def test_a_timeout_is_not_retried_as_if_it_were_a_bad_generation(self, monkeypatch) -> None:
        """A timeout means the request may still be running upstream; retrying it
        blindly doubles the load on a provider that is already slow."""
        from app.services.llm import LLMTimeoutError

        llm = ScriptedLLM([LLMTimeoutError("the model did not respond in time")])
        with pytest.raises(LLMTimeoutError):
            _run_agent(monkeypatch, llm, _settings())
        assert llm.calls == 1

    def test_a_rate_limit_that_outlasts_the_budget_ends_the_investigation(
        self, monkeypatch
    ) -> None:
        from app.services.llm import LLMRateLimitError

        llm = ScriptedLLM([LLMRateLimitError("rate limited")])
        with pytest.raises(LLMRateLimitError):
            _run_agent(monkeypatch, llm, _settings())
        assert llm.calls == 1

    def test_a_partial_investigation_is_recorded_on_the_failure(self, monkeypatch) -> None:
        """Tool activity gathered before the failure is not thrown away."""
        from app.services.llm import LLMRateLimitError

        llm = ScriptedLLM([_wants(), LLMRateLimitError("rate limited")])
        with pytest.raises(LLMRateLimitError):
            _run_agent(monkeypatch, llm, _settings())

    def test_the_step_limit_reports_what_the_investigation_did(self, monkeypatch) -> None:
        llm = ScriptedLLM([_wants()] * 2)
        with pytest.raises(InvestigationFailed) as exc:
            _run_agent(monkeypatch, llm, _settings(AGENT_MAX_STEPS=2, AGENT_MAX_TOOL_CALLS=25))

        assert exc.value.steps == 2
        assert exc.value.tool_calls_used == 2
        assert exc.value.model == "fake-model"


def _run_agent(
    monkeypatch,
    llm: ScriptedLLM,
    settings: Settings,
    *,
    executed: list[str] | None = None,
    validate: Any = None,
):
    """Drive the loop with retrieval, tool execution and the database stubbed out."""
    from app.services import agent as agent_module
    from app.services.retrieval import RetrievalResult, RetrievalStrength

    monkeypatch.setattr(
        agent_module,
        "retrieve",
        lambda *a, **k: RetrievalResult(
            sources=[], strength=RetrievalStrength.NONE, model="m", searched_chunks=0
        ),
    )

    def fake_execute(context: ToolContext, call: ToolCall) -> ToolResult:
        if executed is not None:
            executed.append(call.id)
        context.files_read["a.py"] = "old\n"
        context.cite(path="a.py", start_line=1, end_line=1, symbol=None, origin="read_file")
        return ToolResult(tool_use_id=call.id, content='{"results": []}')

    monkeypatch.setattr(agent_module, "execute", fake_execute)
    monkeypatch.setattr(
        agent_module,
        "validate_patch",
        validate
        or (
            lambda *a, **k: ValidatedPatch(
                files=[FilePatch(path="a.py", original="old\n", updated="new\n")]
            )
        ),
    )

    # The result cites S1, which exists once any tool has run; register it up
    # front so a direct conclusion is grounded too.
    real_new_context = agent_module.new_context

    def seeded_context(*args: Any, **kwargs: Any) -> ToolContext:
        context = real_new_context(*args, **kwargs)
        context.cite(path="a.py", start_line=1, end_line=1, symbol=None, origin="read_file")
        return context

    monkeypatch.setattr(agent_module, "new_context", seeded_context)

    class _Repo:
        id = uuid.uuid4()
        owner = "acme"
        name = "api"
        default_branch = "main"
        indexed_commit_sha = "abc"

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
            "executing",
            "committed",
            "pr_created",
        }


class TestErrorCodesAreDistinct:
    def test_each_patch_failure_has_its_own_code(self) -> None:
        """The UI needs to tell 'model misquoted' apart from 'out of scope' and 'invalid'."""
        assert PatchMismatchError("x").code == "patch_mismatch"
        assert PatchScopeError("x").code == "patch_out_of_scope"
        assert PatchError("x").code == "patch_invalid"
