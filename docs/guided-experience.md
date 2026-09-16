# Guided experience

Follows [ux-overhaul.md](ux-overhaul.md). The overhaul made the interface honest and gave every
screen a next step. It still *looked* like "GitHub + ChatGPT": a repository admin list, and an Ask
screen that opened as an empty chat. This pass changes the interaction model so that working in
DevPilot feels like progressing through a repository.

**Principle.** Borrowed from Duolingo, in behaviour only — none of its colours, illustrations or
playfulness: *the next action is always obvious.* On every screen a first-time user should be able
to answer, within about three seconds:

1. Where am I?
2. What has happened?
3. What should I click next?

The visual language stays Linear/GitHub-level restraint: dark, dense, calm, few borders, no
gradients, no invented numbers.

Scope: frontend only. No backend logic changed. Every state shown is derived from real API data.

---

## The journey

```
Connect → Index → Understand → Investigate → Review → Ship
```

Six steps, shown everywhere the user needs orientation. Progression is **shown, never enforced**:
every step stays a link, and future steps are subdued rather than disabled.

### Derivation

`lib/workflow.ts` remains the single source of truth. It has two views of the same state:

| View | Type | Used by | Why it exists |
| --- | --- | --- | --- |
| Stages | `deriveWorkflow() → { stages, next }` | Sidebar, routing, next step | Routes are organised by stage; connect and index share the repository overview |
| Journey | `deriveJourney(workflow, indexingStatus)` | Homepage, overview | "Connected" and "Indexed" are different accomplishments, so users see six steps |

`deriveJourney` maps each stage onto journey steps:

| Journey step | Source | done | current | upcoming |
| --- | --- | --- | --- | --- |
| Connect | the repository record exists | always | — | — |
| Index | `repository` stage | indexed | not indexed, indexing, or failed (`attention`) | — |
| Understand | `ask` stage | ≥ 1 conversation | next action is to ask | otherwise |
| Investigate | `investigate` stage | ≥ 1 proposal of any status | next action is to investigate | otherwise |
| Review | `review` stage | changes reviewed, none waiting | a proposal awaits a decision | otherwise |
| Ship | `ship` stage | PRs opened, none ready | an approved change is ready | otherwise |

Exactly one step is `current` — the one the workflow's `next` action belongs to. `locked` marks an
upcoming step whose prerequisite is missing (for example, anything after an unindexed repository),
and is rendered more faintly. `attention` marks a failed index, rendered in the danger colour.

`NextAction` gained a `status` field — a few words for where the repository stands ("Ready to
explore", "Change awaiting review", "Indexing failed") — used where there is no room for the full
next-step title.

Tests: `lib/workflow.test.ts` (including "always has exactly one current step").

---

## Screens

### Repositories (home)

*"Here is where you left off. Here's what you do next."*

- **Continue where you left off** — the most recently active repository, with its full journey
  track and a next-step strip carrying the page's **one** primary action.
- **Other repositories** — quiet rows: name, a one-line journey summary
  (`✓ Connected ✓ Indexed ● Ready to explore`), and the next step as a *secondary* button.
- "Connect repository" is a ghost button in the header: always available, never competing.

Recency is real: the latest of the repository's `created_at` / `indexed_at`, its conversations'
`created_at`, and its changes' `created_at` / `reviewed_at` / `executed_at`. The page loads each
repository's workflow context (conversations + changes) to derive this; a failed read counts as
"nothing yet", which can make a step look unstarted but never falsely complete.

Files: `app/(workspace)/page.tsx`, `components/repository/repository-list.tsx`.

### Repository overview

A **Your progress** journey track leads the page (each step links to its screen), followed by the
index state and its next step, recent work, and retrieval diagnostics behind a disclosure.

After a successful index run the heading reads "*{repo}* is ready" and the next step is
**Ask about this repository →**.

### Understand (Ask) — before the first question

The screen no longer opens as an empty chat. It is a guided exploration:

```
UNDERSTAND · STEP 3 OF 6
What do you want to figure out?

START WITH A QUESTION
  How it works           Where things live               What could go wrong
  What happens when…  →  Where are the API routes…    →  What could cause performance…  →
  How does data flow… →  Where is authentication…     →  How are errors handled?        →

OR ASK YOUR OWN QUESTION ───────────────────────────────
  [ What do you want to understand?                    Ask ]

PICK UP A PREVIOUS CONVERSATION
```

- Starting points are grouped by intent and **ask in one click**. They are generic questions any
  codebase can answer — never claims about this repository.
- The input is inline and secondary; it no longer dominates the screen.
- When asking is blocked (not indexed, no provider configured), a next step names the prerequisite
  and links to it, and the starting points are disabled.

### Understand (Ask) — once a conversation exists

The layout switches to a compact developer-assistant view: a slim bar (*Understanding {repo} · N
questions*, History, New conversation), the thread, and a docked follow-up input.

Each answer is a structured report, not a wall of markdown:

| Section | Content |
| --- | --- |
| **Question** | The question as a heading |
| Progress | Real stages from the stream: finding code → found *N* sources → writing |
| **Answer** | Rendered markdown with citation chips |
| **What DevPilot found** | Compact evidence rows, cited sources first |
| **What this means** | The closing summary, set apart |
| **Next step** | **Investigate this code →**, with *Ask another question* as the quiet alternative |

When nothing relevant was found, the next step is *Try a more specific question*; after a failure
it is *Ask again*.

Files: `components/ask/conversation-pane.tsx`, `turn.tsx`, `composer.tsx` (`variant="inline" |
"docked"`).

### Evidence and citations

- **Citations are interactive.** `[S1]` and `[S1, S2]` render as chips naming file, symbol and
  lines. Clicking one opens the source viewer (side panel on wide screens, inline on narrow) with
  real line numbers; Escape closes it.
- **No dead text.** A label with no matching source is removed along with the space before it, so
  "claimed [S7]." reads "claimed.". Real labels in a mixed group are kept (`withKnownCitations`).
- **Evidence rows** show `S1 · Operations · frontend/src/App.jsx · lines 42–61 · Cited ·
  [View code]` in one bordered list, replacing the previous grid of cards.
- **Retrieval diagnostics** — semantic score, chunk type, language, and the first line of each
  chunk — sit behind a toggle.
- Past conversations store citations but not source text; the viewer says so and suggests asking
  again, rather than showing stale code.

Files: `components/ask/answer.tsx`, `evidence.tsx`, `source-viewer.tsx`.

### Investigate, Review, Ship

Each opens with a **stage heading** (`INVESTIGATE · STEP 4 OF 6`) and hands off to the next:

| After | Next step |
| --- | --- |
| Indexing | **Ask about this repository →** |
| An answer | **Investigate this code →** |
| An investigation | **Review proposed change →** |
| Approving a diff | **Create branch & PR** (in the change's footer) |

Investigate offers *Ask a question first →* when there is no prior question or investigation.

---

## Reusable patterns

| Component | Where | Purpose |
| --- | --- | --- |
| `JourneyTrack` (`components/ui/journey.tsx`) | Home, overview | All six steps with done / current / upcoming markers; steps link when `hrefFor` is given |
| `JourneySummary` | Home rows | Accomplishments plus the current status on one line |
| `JourneyMarker` | Track, summary, sidebar | ✓ done, ◉ current, ○ upcoming — one visual vocabulary for progress |
| `NextStep` (`components/ui/next-step.tsx`) | Every stage | A labelled region with an accent rail, one primary action, at most one quiet alternative |
| `Button` / `ButtonLink` `forward` | Every next-step action | Appends a → that nudges on hover; the arrow always means "this moves you forward" |
| `StageHeading` (`components/layout/stage-heading.tsx`) | Understand, Investigate, Review, Ship | "Where am I?": the step's name and position, then the screen's title |
| `EmptyState` (`components/ui/empty-state.tsx`) | Review, Ship, not connected | Dashed outline; answers what is missing, why it matters, and what to click |

**Rules for new screens.** Exactly one `primary` button per region, with `forward` when it advances
the journey. Secondary actions are `ghost` or `secondary`. Empty states always carry an action,
normally the workflow's `next`, so they can never point somewhere the sidebar disagrees with.

---

## Sidebar

Inside a repository the sidebar reads as a path, not a list of routes:

```
RetailHub
EXPLORE
  ✓ Repository      Indexed
  │
  ◉ Understand      2 conversations
BUILD
  ✓ Investigate     1 investigation
  │
  ○ Review          No proposal yet
  │
  ○ Ship            No approved change yet
```

A rail joins the steps within each group and turns green once the step above is done. The next
step's detail is in the accent colour; locked steps are faint. Below `lg` the sidebar collapses to
icons with a dot on the next step. Screen readers hear each step's state ("done", "next step",
"not available yet").

---

## Loading and progress: honesty rules

Loading states explain real progress and never invent it.

| Process | What the API exposes | What the UI shows |
| --- | --- | --- |
| Answering | A stream: sources event, then text | Stages that tick as events arrive |
| Indexing | One synchronous request | What the run does, all marked *running* together, with a real elapsed timer |
| Investigating | One synchronous request | Same as indexing |
| Opening a PR | Recorded execution events | Each step ticks from its recorded event |
| Route loads | Nothing | A skeleton shaped like the journey + heading layout |

A step-by-step indexing checklist (✓ reading, ✓ parsing, ● embedding) was deliberately **not**
built: the index endpoint reports nothing until it finishes, so ticking stages would be fake
progress. It needs a backend progress stream first (see below).

---

## First-time-user walkthrough

Each screen checked against "can I tell what to click next within three seconds?":

| Screen | Where am I? | What happened? | Next click |
| --- | --- | --- | --- |
| Home, no repositories | Header: Repositories | Nothing yet | **Connect repository →**, with the six-step journey below |
| Home, repositories | "Continue where you left off" | Journey track on the lead repository | The only primary button on the page |
| Overview, not indexed | Journey: Index current | "hasn't been indexed yet" | **Index repository →** |
| Overview, indexed | Journey: Understand current | "*repo* is ready", with file and chunk counts | **Ask about this repository →** |
| Understand, fresh | `UNDERSTAND · STEP 3 OF 6` | — | Any starting-point question (one click asks) |
| Understand, answered | Bar: Understanding *repo* | Answer, evidence, meaning | **Investigate this code →** |
| Investigate | `INVESTIGATE · STEP 4 OF 6` | Request pre-filled from the question | **Investigate →**, then **Review proposed change →** |
| Review | `REVIEW · STEP 5 OF 6` | Proposal with diff | **Approve change**, then **Create branch & PR** |
| Ship | `SHIP · STEP 6 OF 6` | Ready or opened PRs | **Create branch & PR** / **View pull request** |
| Any empty stage | Stage heading | Empty state says what is missing | The workflow's next action |

---

## Decisions and deferred work

- **No Activity entry in the sidebar.** The overhaul removed a permanently empty Activity page as a
  dead end. Recent questions and investigations already appear on the repository overview.
  Reintroduce it only with a real activity feed.
- **Indexing progress** needs a backend progress stream (or a polled stage field on the repository)
  before the UI can tick stages honestly.
- **Source text for past answers** isn't stored; showing it would need a chunk-by-id endpoint.
- **Starting points are static.** Tailoring them to a repository (for example, from its detected
  languages or frameworks) is possible from existing data, but should not suggest a feature the
  repository lacks.

## Testing

| Test file | Covers |
| --- | --- |
| `lib/workflow.test.ts` | Stage states, next actions, journey derivation, exactly one current step |
| `components/ui/journey.test.tsx` | Track order and states, links without blocking, summary wording |
| `components/repository/repository-list.test.tsx` | Lead repository with the one primary action, quiet rows, failed index |
| `components/ask/conversation-pane.test.tsx` | Guided start, one-click starting points, blocked state, switch to the conversation layout, report structure, evidence rows, diagnostics toggle, next steps |
| `components/ask/answer.test.tsx` | Citation chips, dropping unmatched labels |
| `components/layout/workflow-nav.test.tsx` | Explore/Build grouping, states, current page |
| `components/changes/*.test.tsx`, `components/repository/index-panel.test.tsx` | Hand-offs and empty states between stages |
