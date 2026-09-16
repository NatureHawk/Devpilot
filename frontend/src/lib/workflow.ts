import type { ChangeStatus, IndexingStatus } from "@/lib/api";

/**
 * DevPilot's workflow, derived from real repository state.
 *
 *   Connect → Index → Understand → Investigate → Review → Ship
 *
 * This is the single place that decides what is done, what is possible, and
 * what the user should do next. Screens and the sidebar only render it, so the
 * "next step" can never disagree between two parts of the interface.
 *
 * Routes are organised by stage (`Stage`): connecting and indexing share the
 * repository overview. The user-facing journey (`JourneyStep`) names all six
 * steps, because "connected" and "indexed" are different accomplishments.
 */

export type StageId = "repository" | "ask" | "investigate" | "review" | "ship";

/**
 * - done: this stage has produced something.
 * - current: the stage the next action belongs to. Exactly one stage is current.
 * - available: possible now, but not the recommended next move.
 * - locked: a prerequisite is missing. Still navigable, shown subdued.
 */
export type StageState = "done" | "current" | "available" | "locked";

export type WorkflowInput = {
  repositoryName: string;
  indexingStatus: IndexingStatus;
  conversationCount: number;
  changeStatuses: ChangeStatus[];
};

export type Stage = {
  id: StageId;
  label: string;
  /** Route segment under the repository; "" is the overview. */
  segment: string;
  state: StageState;
  /** One short line: what has happened here, or what unlocks it. */
  detail: string;
};

export type NextAction = {
  stage: StageId;
  segment: string;
  /** Where the repository stands, in a few words ("Ready to explore"). */
  status: string;
  title: string;
  description: string;
  /** Short verb phrase for the primary button. */
  label: string;
};

export type Workflow = { stages: Stage[]; next: NextAction };

const REVIEWED: ReadonlySet<ChangeStatus> = new Set([
  "approved",
  "rejected",
  "executing",
  "committed",
  "pr_created",
]);
const READY_TO_SHIP: ReadonlySet<ChangeStatus> = new Set(["approved", "committed"]);

function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}

export function deriveWorkflow(input: WorkflowInput): Workflow {
  const { indexingStatus, conversationCount, changeStatuses } = input;
  const indexed = indexingStatus === "indexed";

  const proposed = changeStatuses.filter((status) => status === "proposed").length;
  const reviewed = changeStatuses.filter((status) => REVIEWED.has(status)).length;
  const readyToShip = changeStatuses.filter((status) => READY_TO_SHIP.has(status)).length;
  const executing = changeStatuses.filter((status) => status === "executing").length;
  const shipped = changeStatuses.filter((status) => status === "pr_created").length;

  const next = nextAction(input, { indexed, proposed, readyToShip });

  const stateFor = (id: StageId, done: boolean, locked: boolean): StageState => {
    if (next.stage === id) return "current";
    if (done) return "done";
    return locked ? "locked" : "available";
  };

  const stages: Stage[] = [
    {
      id: "repository",
      label: "Repository",
      segment: "",
      state: stateFor("repository", indexed, false),
      detail: INDEX_DETAIL[indexingStatus],
    },
    {
      id: "ask",
      label: "Understand",
      segment: "ask",
      state: stateFor("ask", conversationCount > 0, !indexed),
      detail: !indexed
        ? "After indexing"
        : conversationCount > 0
          ? plural(conversationCount, "conversation")
          : "Ask a question",
    },
    {
      id: "investigate",
      label: "Investigate",
      segment: "changes",
      state: stateFor("investigate", changeStatuses.length > 0, !indexed),
      detail: !indexed
        ? "After indexing"
        : changeStatuses.length > 0
          ? plural(changeStatuses.length, "investigation")
          : "Investigate a change",
    },
    {
      id: "review",
      label: "Review",
      segment: "review",
      state: stateFor("review", reviewed > 0 && proposed === 0, proposed + reviewed === 0),
      detail:
        proposed > 0
          ? `${plural(proposed, "proposal")} to review`
          : reviewed > 0
            ? plural(reviewed, "change") + " reviewed"
            : "No proposal yet",
    },
    {
      id: "ship",
      label: "Ship",
      segment: "pull-requests",
      state: stateFor(
        "ship",
        shipped > 0 && readyToShip === 0,
        readyToShip + executing + shipped === 0,
      ),
      detail:
        readyToShip > 0
          ? `${plural(readyToShip, "change")} ready`
          : shipped > 0
            ? `${plural(shipped, "pull request")} opened`
            : "No approved change yet",
    },
  ];

  return { stages, next };
}

const INDEX_DETAIL: Record<IndexingStatus, string> = {
  not_indexed: "Not indexed yet",
  indexing: "Indexing…",
  indexed: "Indexed",
  failed: "Indexing failed",
};

/** The step that follows a finished index run, whatever the server saw before it. */
export function askNextAction(repositoryName: string): NextAction {
  return {
    stage: "ask",
    segment: "ask",
    status: "Ready to explore",
    title: `Understand how ${repositoryName} works`,
    description:
      "Ask how something works or where it lives. Every answer cites the exact files and lines it comes from.",
    label: "Ask about this repository",
  };
}

function nextAction(
  input: WorkflowInput,
  counts: { indexed: boolean; proposed: number; readyToShip: number },
): NextAction {
  const { repositoryName, indexingStatus, conversationCount, changeStatuses } = input;

  if (indexingStatus === "failed") {
    return {
      stage: "repository",
      segment: "",
      status: "Indexing failed",
      title: "Indexing didn't finish",
      description:
        "The last run failed. Retry it so DevPilot can search the code and answer questions from it.",
      label: "Retry indexing",
    };
  }
  if (indexingStatus === "indexing") {
    return {
      stage: "repository",
      segment: "",
      status: "Indexing…",
      title: `${repositoryName} is being indexed`,
      description: "A run is already in progress. Questions become available when it finishes.",
      label: "View indexing",
    };
  }
  if (!counts.indexed) {
    return {
      stage: "repository",
      segment: "",
      status: "Ready to index",
      title: `Index ${repositoryName}`,
      description:
        "DevPilot reads the default branch and prepares its code for search, so answers come from the repository itself.",
      label: "Index repository",
    };
  }
  if (counts.readyToShip > 0) {
    return {
      stage: "ship",
      segment: "pull-requests",
      status: "Ready to ship",
      title: "Ship the approved change",
      description:
        "A reviewed change is ready. DevPilot creates a branch, commits the exact approved diff, and opens a pull request.",
      label: "Create branch & PR",
    };
  }
  if (counts.proposed > 0) {
    return {
      stage: "review",
      segment: "review",
      status: "Change awaiting review",
      title: "Review the proposed change",
      description: "Read the diff and the investigation behind it, then approve or reject it.",
      label: "Review proposed change",
    };
  }
  if (conversationCount === 0) {
    return askNextAction(repositoryName);
  }
  if (changeStatuses.length === 0) {
    return {
      stage: "investigate",
      segment: "changes",
      status: "Ready to investigate",
      title: "Investigate a change",
      description:
        "Describe what should change. DevPilot reads the relevant code and proposes a diff for you to review.",
      label: "Investigate a change",
    };
  }
  return {
    stage: "ask",
    segment: "ask",
    status: "Keep exploring",
    title: `Keep exploring ${repositoryName}`,
    description: "Ask another question, or investigate a new change from what you learn.",
    label: "Ask another question",
  };
}

/* -------------------------------------------------------------------------
   The journey: the same state, told as six steps.
   ------------------------------------------------------------------------- */

export type JourneyStepId = "connect" | "index" | "understand" | "investigate" | "review" | "ship";

/**
 * - done: completed at least once.
 * - current: where the next action is. Exactly one step is current.
 * - upcoming: not started. `locked` says a prerequisite is still missing.
 */
export type JourneyState = "done" | "current" | "upcoming";

export type JourneyStep = {
  id: JourneyStepId;
  /** The step's name ("Index"). */
  label: string;
  /** The step as an accomplishment ("Indexed"). */
  doneLabel: string;
  state: JourneyState;
  locked: boolean;
  /** The current step needs attention (a failed run), not just a click. */
  attention: boolean;
  /** Route segment under the repository. */
  segment: string;
};

const JOURNEY: { id: JourneyStepId; stage: StageId; label: string; doneLabel: string }[] = [
  { id: "connect", stage: "repository", label: "Connect", doneLabel: "Connected" },
  { id: "index", stage: "repository", label: "Index", doneLabel: "Indexed" },
  { id: "understand", stage: "ask", label: "Understand", doneLabel: "Explored" },
  { id: "investigate", stage: "investigate", label: "Investigate", doneLabel: "Investigated" },
  { id: "review", stage: "review", label: "Review", doneLabel: "Reviewed" },
  { id: "ship", stage: "ship", label: "Ship", doneLabel: "Shipped" },
];

export function deriveJourney(workflow: Workflow, indexingStatus: IndexingStatus): JourneyStep[] {
  return JOURNEY.map((step) => {
    const stage = workflow.stages.find((candidate) => candidate.id === step.stage);
    // A repository record exists, so connecting is always done.
    const state: JourneyState =
      step.id === "connect"
        ? "done"
        : stage?.state === "current"
          ? "current"
          : stage?.state === "done"
            ? "done"
            : "upcoming";

    return {
      id: step.id,
      label: step.label,
      doneLabel: step.doneLabel,
      state,
      locked: stage?.state === "locked",
      attention: step.id === "index" && indexingStatus === "failed",
      segment: stage?.segment ?? "",
    };
  });
}
