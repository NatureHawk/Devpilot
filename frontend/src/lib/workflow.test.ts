import { describe, expect, it } from "vitest";

import type { ChangeStatus, IndexingStatus } from "@/lib/api";
import {
  deriveJourney,
  deriveWorkflow,
  type JourneyState,
  type JourneyStepId,
  type StageId,
  type StageState,
} from "@/lib/workflow";

function workflow(
  indexingStatus: IndexingStatus,
  conversationCount = 0,
  changeStatuses: ChangeStatus[] = [],
) {
  return deriveWorkflow({
    repositoryName: "RetailHub",
    indexingStatus,
    conversationCount,
    changeStatuses,
  });
}

function states(result: ReturnType<typeof workflow>): Record<StageId, StageState> {
  return Object.fromEntries(result.stages.map((stage) => [stage.id, stage.state])) as Record<
    StageId,
    StageState
  >;
}

describe("deriveWorkflow", () => {
  it("starts at indexing and locks everything that needs an index", () => {
    const result = workflow("not_indexed");

    expect(result.next).toMatchObject({ stage: "repository", label: "Index repository" });
    expect(states(result)).toEqual({
      repository: "current",
      ask: "locked",
      investigate: "locked",
      review: "locked",
      ship: "locked",
    });
  });

  it("offers a retry after a failed run", () => {
    expect(workflow("failed").next).toMatchObject({ stage: "repository", label: "Retry indexing" });
  });

  it("points to Ask once indexed and nothing has been asked", () => {
    const result = workflow("indexed");

    expect(result.next).toMatchObject({
      stage: "ask",
      segment: "ask",
      label: "Ask about this repository",
    });
    expect(states(result)).toMatchObject({
      repository: "done",
      ask: "current",
      investigate: "available",
      review: "locked",
      ship: "locked",
    });
  });

  it("points to Investigate after a question", () => {
    const result = workflow("indexed", 2);

    expect(result.next).toMatchObject({ stage: "investigate", label: "Investigate a change" });
    expect(result.stages.find((stage) => stage.id === "ask")).toMatchObject({
      state: "done",
      detail: "2 conversations",
    });
  });

  it("points to Review while a proposal awaits a decision", () => {
    const result = workflow("indexed", 1, ["proposed"]);

    expect(result.next).toMatchObject({ stage: "review", label: "Review proposed change" });
    expect(states(result)).toMatchObject({
      investigate: "done",
      review: "current",
      ship: "locked",
    });
  });

  it("points to Ship once a change is approved, ahead of other proposals", () => {
    const result = workflow("indexed", 1, ["proposed", "approved"]);

    expect(result.next).toMatchObject({ stage: "ship", label: "Create branch & PR" });
    expect(result.stages.find((stage) => stage.id === "ship")?.detail).toBe("1 change ready");
  });

  it("treats a committed change whose PR failed as still ready to ship", () => {
    expect(workflow("indexed", 1, ["committed"]).next.stage).toBe("ship");
  });

  it("marks shipping done when every approved change has a pull request", () => {
    const result = workflow("indexed", 3, ["pr_created"]);

    expect(states(result)).toMatchObject({ review: "done", ship: "done" });
    expect(result.next.stage).toBe("ask");
    expect(result.stages.find((stage) => stage.id === "ship")?.detail).toBe(
      "1 pull request opened",
    );
  });

  it("does not unlock review for proposals that went stale or failed", () => {
    expect(states(workflow("indexed", 1, ["stale", "failed"])).review).toBe("locked");
  });

  it("always has exactly one current stage", () => {
    const cases: [IndexingStatus, number, ChangeStatus[]][] = [
      ["not_indexed", 0, []],
      ["indexed", 0, ["proposed"]],
      ["indexed", 5, ["approved", "pr_created"]],
      ["indexed", 5, ["rejected"]],
    ];
    for (const [status, conversations, changes] of cases) {
      const current = workflow(status, conversations, changes).stages.filter(
        (stage) => stage.state === "current",
      );
      expect(current).toHaveLength(1);
    }
  });
});

function journey(
  indexingStatus: IndexingStatus,
  conversationCount = 0,
  changeStatuses: ChangeStatus[] = [],
): Record<JourneyStepId, JourneyState> {
  const steps = deriveJourney(
    workflow(indexingStatus, conversationCount, changeStatuses),
    indexingStatus,
  );
  return Object.fromEntries(steps.map((step) => [step.id, step.state])) as Record<
    JourneyStepId,
    JourneyState
  >;
}

describe("deriveJourney", () => {
  it("counts a connected, unindexed repository as connected with indexing next", () => {
    expect(journey("not_indexed")).toEqual({
      connect: "done",
      index: "current",
      understand: "upcoming",
      investigate: "upcoming",
      review: "upcoming",
      ship: "upcoming",
    });
  });

  it("moves to Understand once indexed", () => {
    expect(journey("indexed")).toMatchObject({
      connect: "done",
      index: "done",
      understand: "current",
      investigate: "upcoming",
    });
  });

  it("follows the workflow's next step through review", () => {
    expect(journey("indexed", 2, ["proposed"])).toMatchObject({
      understand: "done",
      investigate: "done",
      review: "current",
      ship: "upcoming",
    });
  });

  it("flags a failed index as needing attention, and locks what depends on it", () => {
    const steps = deriveJourney(workflow("failed"), "failed");
    const index = steps.find((step) => step.id === "index");
    expect(index).toMatchObject({ state: "current", attention: true });
    expect(steps.find((step) => step.id === "understand")?.locked).toBe(true);
  });

  it("always has exactly one current step", () => {
    const cases: [IndexingStatus, number, ChangeStatus[]][] = [
      ["not_indexed", 0, []],
      ["indexed", 0, []],
      ["indexed", 1, []],
      ["indexed", 3, ["approved"]],
      ["indexed", 3, ["pr_created"]],
    ];
    for (const [status, conversations, changes] of cases) {
      const current = Object.values(journey(status, conversations, changes)).filter(
        (state) => state === "current",
      );
      expect(current).toHaveLength(1);
    }
  });
});
