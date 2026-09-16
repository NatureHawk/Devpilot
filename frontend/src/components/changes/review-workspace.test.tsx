import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ReviewWorkspace } from "@/components/changes/review-workspace";
import type { ChangeStatus, ProposedChange } from "@/lib/api";
import { deriveWorkflow } from "@/lib/workflow";

vi.mock("@/app/actions", () => ({
  executeChangeAction: vi.fn(),
  reviewChangeAction: vi.fn(),
}));

function change(id: string, status: ChangeStatus): ProposedChange {
  return {
    id,
    repository_id: "repo-1",
    conversation_id: null,
    status,
    request: `Request ${id}`,
    summary: "",
    diff: "",
    files_changed: 0,
    tool_calls_used: 1,
    indexed_commit_sha: null,
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
  };
}

describe("ReviewWorkspace", () => {
  it("sends you to ask first when nothing has been asked yet", () => {
    render(
      <ReviewWorkspace
        owner="acme"
        name="widgets"
        initialChanges={[]}
        next={
          deriveWorkflow({
            repositoryName: "widgets",
            indexingStatus: "indexed",
            conversationCount: 0,
            changeStatuses: [],
          }).next
        }
      />,
    );

    expect(screen.getByRole("heading", { name: "No proposals to review yet" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Ask about this repository" })).toHaveAttribute(
      "href",
      "/repositories/acme/widgets/ask",
    );
  });

  it("teaches the prerequisite when there is nothing to review", () => {
    render(<ReviewWorkspace owner="acme" name="widgets" initialChanges={[]} />);

    expect(screen.getByText("No proposals to review yet")).toBeVisible();
    expect(screen.getByRole("link", { name: "Investigate a change" })).toHaveAttribute(
      "href",
      "/repositories/acme/widgets/changes",
    );
  });

  it("groups proposals by what they need, most urgent first", () => {
    render(
      <ReviewWorkspace
        owner="acme"
        name="widgets"
        initialChanges={[
          change("shipped", "pr_created"),
          change("approved", "approved"),
          change("proposed", "proposed"),
          change("rejected", "rejected"),
        ]}
      />,
    );

    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings).toEqual([
      "Needs your review · 1",
      "Approved — ready to ship · 1",
      "Shipped · 1",
    ]);
    const needsReview = screen.getByRole("region", { name: /Needs your review/ });
    expect(within(needsReview).getByRole("heading", { name: "Request proposed" })).toBeVisible();
    expect(screen.getByText("Closed · 1")).toBeVisible();
  });
});
