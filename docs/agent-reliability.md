# Agent reliability

What happens when the change workflow is run against many real tasks rather
than one. The end-to-end pipeline was proven once in the previous milestone;
this is the attempt to find out how often it actually works, and where it
breaks.

## Method

Twelve tasks across three indexed repositories, every one written after reading
the code it concerns, so the expected outcome could be checked against the
source rather than guessed:

| Repository | Language | What it is |
| --- | --- | --- |
| `NatureHawk/RetailHub` | Python + React | FastAPI analytics API over SQLite, Vite dashboard |
| `NatureHawk/SwipeSort` | TypeScript | React Native media-sorting app |
| `NatureHawk/ambasuu-power-hub` | TypeScript | Marketing site |

Each task declares `expect: change` or `expect: no_change`. A task expecting a
change is only counted as passed when a validated patch was produced; a task
expecting none is only counted as passed when the system declined and said why.
The rubric — whether the change is *correct*, not merely well-formed — is
checked by reading each diff against the source afterwards.

Every run is recorded whole: retrieval seeds, steps, tool calls by name,
outcome, cited evidence, files, anchor count, diff verification, latency, and
the provider-level 429s and retries that happened underneath.

## The free-tier wall

The headline constraint is not the agent. It is that one investigation costs
3–9 model requests and 6,000–8,000 tokens, and every free tier prices that
badly:

| Provider / model | Free-tier ceiling | Investigations/day | How it fails |
| --- | --- | --- | --- |
| Groq `openai/gpt-oss-120b` | 200,000 tokens/day; 8,000 tokens/minute | ~25–30 | TPD exhausted; `retry-after` grows to 1,463s |
| Gemini `gemini-3.6-flash` | 20 requests/day, per model | ~3–5 | Quota error on the 21st request |
| Gemini `gemini-3.5-flash` | 20 requests/day, per model | ~3–5 | Same |
| OpenRouter `qwen3.8-27b:free` | upstream provider limit | varies hourly | Upstream 429, unrelated to the account |
| OpenRouter `nex-n2.5-pro:free` | upstream provider limit | usable | Slow: up to ~3.5 min per turn |

Groq's 8,000 tokens per *minute* is the harshest of these, because it is a hard
per-request ceiling as well as a rate: no single investigation turn may exceed
it, which caps how much source one investigation can hold. Fitting under it
required cutting the product defaults roughly in half (`CONTEXT_MAX_CHARS`,
`AGENT_MAX_TOOL_OUTPUT_CHARS`, `AGENT_MAX_TOOL_CALLS`).

Because no single free tier could carry twelve investigations in a day, the run
is split across providers, and every result below records the model that
produced it. That is a limitation of the measurement, stated rather than hidden:
task difficulty and model capability are confounded across rows.

## Results

`valid` is what DevPilot guarantees: anchors matched exactly once, diff
re-applied and verified, citations resolved. `correct` is a human reading the
diff against the source afterwards — a thing the system cannot check.

| Task | Type | Model | Outcome | Valid | Correct | Steps/Tools | Files | Latency |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T01 | Bug fix: unclosed SQLite connections | gemini-3.5-flash | change_proposed | yes | yes | 9/8 | api.py | 103s |
| T02 | Add validation | gemini-3.5-flash | change_proposed | yes | yes | 3/2 | api.py | 78s |
| T03 | Modify behaviour + missing import | nex-n2.5-pro | change_proposed | yes | yes | 2/1 | api.py | 45s |
| T04 | Refactor | nex-n2.5-pro | change_proposed | yes | yes | 2/2 | api.py | 58s |
| T05 | Performance | nex-n2.5-pro | change_proposed | yes | yes | 2/1 | api.py | 114s |
| T06 | Multi-file | nex-n2.5-pro | change_proposed | yes | yes | 6/8 | api.py + App.jsx | 530s |
| T07 | Add helper + use it (multi-file) | nex-n2.5-pro | change_proposed | yes | yes | 6/8 | format.ts + SwipeCard.tsx | 154s |
| T08 | Investigation required | nex-n2.5-pro | change_proposed | yes | **no** | 4/8 | api.py + App.jsx | 107s |
| T09 | Question, no change | nex-n2.5-pro | unsupported_request | n/a | yes | 2/1 | — | 82s |
| T10 | Unsupported request | nex-n2.5-pro | unsupported_request | n/a | yes | 1/0 | — | 23s |
| T11 | Nonexistent functionality | nex-n2.5-pro | insufficient_evidence | n/a | yes | 4/8 | — | 69s |
| T12 | Cannot be safely inferred | nex-n2.5-pro | insufficient_evidence | n/a | yes | 4/8 | — | 194s |

Totals, counted rather than estimated:

- 12/12 tasks returned a result
- 8/8 change tasks produced a patch, and each touched **exactly** the files expected
- 8/8 patches: anchors exact, diff verified, zero unresolved citations
- 11/11 patched files re-apply cleanly and still parse (Python) or balance (TS/JSX)
- 4/4 no-change tasks declined correctly, with a specific account of what was missing
- **7/8 patches are actually correct**; one is valid but broken
- Latency: 23s min, 103s median, 530s max
- Approval was enforced on every path; no GitHub write happened without it

### The one wrong patch

T08 asked why the Overview "total orders" KPI was too low. The investigation was
better than the task asked for: it found the planted cause (the KPI sums only
the five products the backend returns) *and* read the ETL pipeline to discover
that `Fact_Sales` holds one row per line item, so orders must be counted as
`COUNT(DISTINCT transaction_id)`. It then wrote:

```sql
SELECT COUNT(DISTINCT transaction_id) as val FROM (<existing subquery>)
```

The existing subquery selects only `date_key, total_amount, product_key`, so
that query raises `no such column: transaction_id` at runtime.

Every structural gate passed: the anchors matched exactly once, the diff was
verified against the patched file, the citations resolved, and the file still
parses. None of that can catch a semantic error, because nothing here executes
the code. This is the clearest argument in the evaluation for why approval is
mandatory and why the diff is what a human reviews.

### Code additions

The previous milestone needed a prompt change before the model would treat
*adding* code as a valid fix. That now holds without further nudging: T02 added
validation, T03 added an import, T07 added a function, an import, a conditional
and a UI element in one change, and T01 added a `try/finally` and rewired seven
endpoints. None required special-casing.

## Bugs this found

Four real defects, all of which survived the previous milestone because that
milestone only ever ran one provider down one happy path.

1. **Skipped-range blindness.** Models paginate a file by hand and miscount. On
   `api.py` the model read lines 1–120, then 150–240, never saw line 127, and
   reported the code as absent. Tools now report the exact unread line ranges
   after every read, and name the line to resume from. The task went from
   `insufficient_evidence` to a correct two-anchor patch.
2. **Gemini tool calling never worked.** `functionDeclarations.parameters` takes
   an OpenAPI subset, not JSON Schema, and rejects `additionalProperties`
   outright — so every agent request to Gemini 400'd. Tool schemas are now
   translated to Gemini's subset on the way out. The provider had advertised
   `tools: True` throughout.
3. **A malformed request reported as "too large".** Both Groq and Gemini use 400
   for several unrelated conditions. Groq's `output_parse_failed` (the provider
   failing to parse its own model's output) and Gemini's `INVALID_ARGUMENT` were
   both surfaced as "the request may be too large — try a narrower question",
   which sends the operator to fix the wrong thing. They are now separated by
   what the response actually says.
4. **OpenRouter never retried.** Groq and Gemini both had bounded retry; the
   OpenRouter provider had none, so a single 429 on any turn ended the whole
   investigation instantly. It now retries on the same terms as the others.

A fifth, smaller fix: when a provider asks for a longer wait than the retry
budget allows, all three clients used to sleep out the entire budget and then
fail anyway. They now give up immediately, which turns a four-minute stall into
a four-second answer.

## The GitHub write path, re-verified

One evaluation proposal (T03) was taken through the whole path again on
`NatureHawk/RetailHub`:

| Step | Result |
| --- | --- |
| Execute before approval | 409 `conflict` — refused |
| Approve | 200, status `approved`, nothing written |
| Execute | branch `devpilot/change/178e1cab-…`, commit `8c702d5a`, PR [#1](https://github.com/NatureHawk/RetailHub/pull/1) |
| Execute again | same PR #1 returned, nothing created |
| GitHub | PR open, 1 file, +2 −1 |

## Remaining limitations

- Results are split across providers and models, so the table measures the
  *system*, not one model's competence.
- Free-tier rate limits, not agent quality, are the dominant failure cause in
  the raw run. Failures are labelled by cause so the two are not conflated.
- Latency is dominated by the provider: 23s–530s per investigation on
  OpenRouter's `nex-n2.5-pro` (multi-file tasks are the slow end), 78–103s on
  `gemini-3.5-flash`, and 250–360s on Groq where most of the time is spent
  waiting out 429s.
- **Validity is not correctness.** The system proves a patch applies exactly
  where it was quoted and that the diff reproduces the patched file. It does not
  and cannot prove the change works: there is no sandbox, nothing is executed,
  no test is run. One of eight patches in this run was valid and wrong.
- Twelve tasks is a small sample. These totals say the pipeline held across a
  varied set on one day; they are not a success rate to quote.
