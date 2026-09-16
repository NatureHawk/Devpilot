import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InvestigateWorkspace } from "@/components/changes/investigate-workspace";
import type { ProposedChange } from "@/lib/api";

const proposeAction = vi.hoisted(() => vi.fn());
vi.mock("@/app/actions", () => ({ proposeChangeAction: proposeAction }));

function proposal(overrides: Partial<ProposedChange> = {}): ProposedChange {
  return {
    id: "change-7",
    repository_id: "repo-1",
    conversation_id: "conv-9",
    status: "proposed",
    request: "Memoise the category list",
    summary: "Wraps the category computation in useMemo.",
    diff: "--- a/web/App.jsx\n+++ b/web/App.jsx\n@@ -1 +1 @@\n-a\n+b",
    files_changed: 1,
    tool_calls_used: 4,
    indexed_commit_sha: "abc1234",
    model: null,
    error: null,
    investigation: [],
    created_at: "2026-09-13T00:00:00Z",
    reviewed_at: null,
    branch_name: null,
    commit_sha: null,
    pr_number: null,
    pr_url: null,
    executed_at: null,
    execution_error: null,
    execution_events: [],
    ...overrides,
  };
}

function renderWorkspace(props: Partial<Parameters<typeof InvestigateWorkspace>[0]> = {}) {
  return render(
    <InvestigateWorkspace
      repositoryId="repo-1"
      owner="acme"
      name="widgets"
      disabledReason={null}
      blockedAction={null}
      initialRequest=""
      conversationId={null}
      recent={[]}
      {...props}
    />,
  );
}

beforeEach(() => {
  proposeAction.mockReset();
});

describe("InvestigateWorkspace", () => {
  it("continues from a question: pre-filled, and linked to its conversation", async () => {
    const user = userEvent.setup();
    proposeAction.mockResolvedValue({ ok: true, data: proposal() });
    renderWorkspace({ initialRequest: "Where is useMemo used?", conversationId: "conv-9" });

    expect(screen.getByLabelText("What should change?")).toHaveValue("Where is useMemo used?");
    expect(screen.getByText(/Started from your question in Ask/)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Investigate" }));
    expect(proposeAction).toHaveBeenCalledWith(
      "repo-1",
      "acme",
      "widgets",
      "Where is useMemo used?",
      "conv-9",
    );
  });

  it("explains what is happening while the investigation runs", async () => {
    const user = userEvent.setup();
    proposeAction.mockImplementation(() => new Promise(() => {}));
    renderWorkspace({ initialRequest: "Memoise the category list" });

    await user.click(screen.getByRole("button", { name: "Investigate" }));

    const stages = await screen.findByRole("list", { name: "Investigation" });
    expect(stages).toHaveTextContent("Retrieving the relevant code");
    expect(screen.queryByText("(done)")).toBeNull();
    expect(screen.getByText(/Running for/)).toBeVisible();
  });

  it("hands a completed investigation to review", async () => {
    const user = userEvent.setup();
    proposeAction.mockResolvedValue({ ok: true, data: proposal() });
    renderWorkspace({ initialRequest: "Memoise the category list" });

    await user.click(screen.getByRole("button", { name: "Investigate" }));

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Investigation complete" })).toBeVisible(),
    );
    expect(screen.getByRole("link", { name: "Review proposed change" })).toHaveAttribute(
      "href",
      "/repositories/acme/widgets/review#change-change-7",
    );
  });

  it("offers to refine the request when no change was proposed", async () => {
    const user = userEvent.setup();
    proposeAction.mockResolvedValue({
      ok: true,
      data: proposal({ status: "failed", diff: "", error: "The quoted text was not found." }),
    });
    renderWorkspace({ initialRequest: "Make it faster" });

    await user.click(screen.getByRole("button", { name: "Investigate" }));

    expect(await screen.findByRole("heading", { name: "No change was proposed" })).toBeVisible();
    expect(screen.getByText("The quoted text was not found.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Edit request" }));
    expect(screen.getByLabelText("What should change?")).toHaveValue("Make it faster");
  });

  it("points to the prerequisite when investigating is blocked", () => {
    renderWorkspace({
      disabledReason: "This repository needs indexing first.",
      blockedAction: { label: "Index repository", href: "/repositories/acme/widgets" },
    });

    expect(screen.queryByLabelText("What should change?")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Index repository" })).toBeVisible();
  });
});
