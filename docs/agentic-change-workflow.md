# The agentic change workflow

How a question becomes a reviewed, validated diff — and what stops it becoming
anything else.

```
user request
    v
retrieval (seed evidence S1..Sn)
    v
investigation loop  <-->  bounded read-only tools (search_code, read_file, find_symbol)
    v
structured result (JSON)  ->  parsed  ->  grounded  ->  anchors validated  ->  diff verified
    v                              |          |               |
proposed change                    +----------+---------------+--> one corrective turn each,
    v                                                             then fail closed
human review (diff)
    v
explicit approval          <- nothing has touched GitHub up to this point
    v
explicit "create pull request"
    v
blobs -> tree -> commit -> branch -> pull request
```

## What the model decides, and what it cannot

The model chooses which tools to call, what to look for, what it concludes, and
what text to replace. It does not choose what a tool may reach, what counts as
evidence, where an edit lands, what the diff says, or whether anything is
written. Those are the backend's, and each is enforced rather than requested:

| Model proposes | Backend enforces |
| --- | --- |
| a tool call | tool must exist; path must be repository-relative; result size and total output capped; every lookup scoped to this repository's rows |
| cited evidence `S3` | must be a span a tool actually returned, else dropped; a change citing none is rejected |
| `old_text` / `new_text` | must match the indexed file exactly once, not overlap another edit, and only in a file opened with `read_file` |
| "here is the change" | diff is rendered from the indexed content and re-applied to prove it reproduces the patched file |
| — | approval and pull-request creation are separate human actions |

## Bounds

Every dimension terminates. `AGENT_MAX_TOOL_CALLS` (8) caps tool use — once
spent, the tools are *withdrawn* from the request, so the next turn can only be
the result. `AGENT_MAX_STEPS` (10) caps model turns. `AGENT_MAX_REPAIR_ATTEMPTS`
(2) caps corrective turns after a rejected result. Tool output is capped per
result and in total, provider-aware: on Groq's free tier the whole investigation
may pull in 14,000 characters, because each turn re-sends the conversation and
the limit there is 8,000 tokens *per minute* — the budget bounds the size of
every later request, not just the sum.

## The result

An investigation returns an outcome, not just prose:

- `change_proposed` — the code read shows the cause and the fix. Carries edits.
- `insufficient_evidence` — it does not. No patch is generated, and the report
  says what was missing. A `low`-confidence proposal is downgraded to this: a
  speculative patch is worse than none.
- `unsupported_request` — not achievable by editing existing indexed files
  (new files, renames, running commands, settings outside the repository).

Confidence is `high` / `medium` / `low` — how directly the evidence supports the
conclusion, never a probability. Stored in `proposed_changes.report` along with
the root cause, cited evidence with locations, anchor line ranges, the blob sha
each file was validated against, and what validation ran.

## Staleness

A patch is only meaningful against the code it was quoted from, so the snapshot
is checked three times, each time against something different:

1. **On read** — the repository's indexed commit sha moved: the proposal is
   marked `stale` so review never shows an untrustworthy diff.
2. **On approval** — sha unchanged, every file still the same blob, every anchor
   still matching exactly once.
3. **Before writing** — the live default branch on GitHub. If it moved but every
   changed file is still the exact blob that was validated, the patch applies
   unchanged and is committed on the live head, so the pull request contains
   only this change. If any changed file differs, nothing is written.

The message always says the same thing: the repository changed, refresh the
investigation.

## Idempotency

Execution is claimed with `SELECT ... FOR UPDATE` (`claim_for_execution`), so two
concurrent requests cannot both write. A crash mid-execution is recovered rather
than duplicated: a proposal stuck in `executing` past ten minutes resumes at the
pull request if a commit and branch were already recorded, otherwise from the
start. Branch names are deterministic per proposal, so a branch an interrupted
attempt created is *adopted* — but only if its tree is exactly the tree this
attempt built; anything else is a conflict and is never overwritten. An open
pull request for the same branch is likewise adopted rather than duplicated,
which also covers a POST that succeeded on GitHub before the response was lost.

## Verified end to end

Run on 2026-09-17 against `NatureHawk/ambasuu-power-hub` (89 files, 569 chunks,
`main` @ `e98e9e5`) with Groq `openai/gpt-oss-120b` — a real model, real
repository data, no mocks anywhere in the path.

Question: the Request a Quote form marks five fields required with an asterisk,
but none of the inputs are actually required.

| Stage | Result |
| --- | --- |
| Retrieval | 3 seed sources (`Contact.tsx`, `ui/form.tsx`) |
| Investigation | 9 model steps, 6 tool calls: `search_code` ×4, `read_file` ×2; tool budget exhausted on the last call |
| Result | `change_proposed`, confidence `medium`, 1 cited source, 0 unresolved citations |
| Validation | 5 anchors, each matching exactly once; diff re-applied and verified |
| Diff | `src/components/Contact.tsx`, +5 −1 |
| Approval | execution before approval refused (409); approved explicitly |
| Write | branch `devpilot/change/871d7320-…`, commit `b23cf12`, PR [#1](https://github.com/NatureHawk/ambasuu-power-hub/pull/1) |
| Idempotency | second execute returned the same PR, created nothing new |

Two real defects surfaced only in this run, both fixed: Groq's 400
`output_parse_failed` (the provider failing to parse its own model's output) was
classified as "request too large" instead of a retryable generation failure; and
a tool-output budget small enough to be spent on one file left the model
concluding `insufficient_evidence` with tool calls still nominally available.

Free-tier note: `openai/gpt-oss-120b` allows 8,000 tokens *per minute*, shared by
prompt and output, so one turn consumes roughly a whole window. The run takes
minutes of wall clock, mostly waiting out 429s, and no single request may exceed
the window at all — hence the small seed context, the per-result cap, and
`GROQ_MAX_RETRY_SECONDS=240`.

## Testing

`tests/test_agent_and_patching.py` holds the bounds, grounding and anchor rules
without a database. `tests/test_change_pipeline.py` drives the real loop against
a real database with a scripted model — real tools, real validation, real diff,
approval through the API — and asserts that no GitHub client is ever constructed
to produce or approve a proposal. `tests/test_execution.py` covers the write
path, staleness and crash recovery against a fake GitHub.

Note: the integration tests wipe users, repositories and proposals. `conftest.py`
redirects them to `<database>_test` unless `DATABASE_URL` is set explicitly, so
running the suite cannot delete a developer's connected repositories.
