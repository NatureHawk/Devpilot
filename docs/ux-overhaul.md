# UX overhaul — audit and plan

> Followed by [guided-experience.md](guided-experience.md), which turns this foundation into a
> guided journey through the repository.

Principle: at any moment a user should know **where they are, what just happened, what to do
next, and what a button will do**. DevPilot is a sequential workflow — connect → index →
understand → investigate → review → ship — and the interface should teach that sequence
instead of presenting a set of dashboard pages.

Scope: frontend only. Backend architecture, retrieval, RAG, indexing and GitHub integration are
unchanged. Every status shown comes from real API state.

## Audit

Journeys inspected: first run (signed out), connect a repository, index it, ask a question, read
the answer and its sources, request a change, review and approve a diff, open a pull request,
settings. Every route and component under `frontend/src` was read.

### 1. The next action is almost never stated

| State | What the screen does today | Problem |
| --- | --- | --- |
| Signed out, dashboard | "Good evening." plus two equally weighted buttons | No single action; the greeting says nothing |
| No repositories | Primary CTA appears twice on the same screen | Duplicated, competing actions |
| Connected, not indexed | Good explanation and "Index repository" | Fine — the one screen that works |
| Indexed | Four uppercase metric boxes and a **Re-index** button | The only action offered is the one you almost never want; nothing points to Ask |
| Answer finished | Answer text ends; nothing follows | No path from understanding to investigating |
| Proposal created | Card appears in a list under the form | "Review" is implied, never asked for |
| Change approved | "Create pull request" is fine, but nothing on other screens says a change is waiting | Shipping depends on finding the right tab |

### 2. Navigation reflects routes, not the workflow

- The global sidebar is Dashboard / Repositories / Activity / Settings — none of which is the work.
- The actual workflow hides in equal-weight horizontal tabs: Overview, Code, Ask, Changes, Pull
  Requests. Nothing shows what is done, what is next, or what is not possible yet.
- **Code** is a permanent placeholder ("No files to browse yet") even after indexing, and
  **Activity** is a permanent empty page. Both are dead ends that look like features.
- Dashboard and Repositories show the same list twice.

### 3. The Ask screen — the most important screen — undersells its result

- The answer is split into paragraphs but markdown is not rendered: real answers arrive with
  `### What the retrieved code shows` and `* **Operations**`, shown literally.
- Citations are tiny `S1` boxes with no file or symbol; `[S1, S2]` was dead text until last
  milestone.
- Sources live in a right panel that only appears at ≥1280px, so on a typical laptop the evidence
  is invisible. Before a question, that panel is filled with a static glossary.
- The left "Scope" rail holds only the branch name and a promise that conversations "are kept
  here" — though past conversations are never loaded.
- Status is a single spinner line; there is no next step after the answer.
- Retrieval scores are shown to everyone, on every source.

### 4. Changes and pull requests lack progression

- Requesting, reviewing and shipping share one long page; the form and the history compete.
- "Investigating…" gives no sense of what is happening during a request that can take a minute.
- The empty state speaks in product language ("AI-generated modifications").
- Pull Requests duplicates status already visible on Changes, without an action.

### 5. Noise and stale copy

- Repository overview leads with the retrieval inspector — a diagnostic tool — and a decorative
  language bar chart.
- Settings describes the product of three milestones ago ("used only once retrieval and answering
  are implemented", "write access is not requested") and ends with an empty Preferences panel.
- Every surface is a bordered panel with a header, so nothing stands out.
- Buttons have primary/secondary/ghost, but no destructive treatment; Reject looks like Approve's
  sibling.
- Loading states are generic skeletons or "Loading".

## What changes, and why

| Change | Why it helps |
| --- | --- |
| **Workflow model** (`lib/workflow.ts`): derives each stage's state — done, current, available, locked — and the single next action from the repository's indexing status, its conversations and its proposals | One source of truth for "where am I / what next"; nothing is hardcoded |
| **Workflow sidebar** inside a repository: Repository, Ask, Investigate, Review, Ship, each with its real state and a one-line detail; locked steps are subdued but still navigable | The sidebar teaches the sequence without documentation; replaces the equal-weight tabs |
| **Next step block** on every workflow screen, with one dominant action and at most one quiet secondary | The next action no longer has to be inferred |
| **Repository overview** leads with identity → index state → next step → recent work; retrieval inspector moves behind "Retrieval diagnostics" | Normal users see the path; developers still get the instrument |
| **Ask result as an investigation report**: Question → Answer (real markdown) → What DevPilot found (evidence cards) → What this means → Next step | Scannable, evidence visually distinct from prose, and it continues into investigation |
| **Citation chips** naming file, symbol and lines; clicking opens a source viewer (side panel on wide screens, inline below) with line numbers; grouped citations become separate chips | Citations become navigation into evidence |
| **Progressive disclosure** for retrieval scores ("Show retrieval details") and investigation steps | Detail is available without being in the way |
| **Honest processing states**: Ask shows real stages from the stream (retrieving → writing → done); indexing and investigation, which run as one request, list what the run does with a real elapsed timer — no fake per-step ticks or percentages | Users know what DevPilot is doing without being misled |
| **Investigate → Review → Ship split** into three screens that hand off to each other ("Review proposed change", "Create branch & PR") | Each screen has one job and one primary action |
| **Answers hand off to investigation** with the question pre-filled and the conversation linked (the API already accepts `conversation_id`) | Investigation feels like a continuation, not a separate app |
| **Contextual empty states** that name the prerequisite and link to it | Empty states become onboarding |
| **Button hierarchy** adds a destructive variant, a large size for next-step CTAs, pressed states, and verb labels | Visual weight matches importance |
| **Removed**: Code tab placeholder, Activity placeholder, greeting, duplicate dashboard list, glossary panels, stale settings copy, empty Preferences panel | Less to scan, nothing that pretends to be a feature |

## Constraints kept

- Next.js App Router, TypeScript, Tailwind; no new UI framework.
- Backend changes are limited to two things the redesign depends on:
  - one prompt style line asking for a short closing "What this means" section, rendered
    separately when present;
  - a routing fix. `GET /repositories/{owner}/{name}` was registered before, and so captured,
    `GET /repositories/{id}/conversations` and `GET /repositories/{id}/changes`. Both lists had
    always answered 404, so the old Changes page never showed saved proposals after a reload and
    the workflow could not count real conversations. The Ask router is now registered first and
    those routes only match a UUID id, so a repository literally named `changes` still resolves.
    Covered by `tests/test_route_resolution.py`.
- Reduced motion respected; focus visible everywhere; live regions for processing states.
