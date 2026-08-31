import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChangesWorkspace } from "@/components/changes/changes-workspace";
import type { ProposedChange } from "@/lib/api";

const executeAction = vi.hoisted(() => vi.fn());
const reviewAction = vi.hoisted(() => vi.fn());
const proposeAction = vi.hoisted(() => vi.fn());
vi.mock("@/app/actions", () => ({
  executeChangeAction: executeAction,
  reviewChangeAction: reviewAction,
  proposeChangeAction: proposeAction,
}));

function change(overrides: Partial<ProposedChange> = {}): ProposedChange {
  return {
    id: "change-1",
    repository_id: "repo-1",
    conversation_id: null,
    status: "proposed",
    request: "Add input validation",
    summary: "Validates the registration payload before use.",
    diff: "",
    files_changed: 1,
    tool_calls_used: 3,
    indexed_commit_sha: "abc1234",
    model: "claude-opus-5",
    error: null,
    investigation: [],
    created_at: "2026-08-28T00:00:00Z",
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

function renderWorkspace(changes: ProposedChange[]) {
  return render(
    <ChangesWorkspace
      repositoryId="repo-1"
      owner="acme"
      name="widgets"
      initialChanges={changes}
      disabledReason={null}
    />,
  );
}

beforeEach(() => {
  executeAction.mockReset();
  reviewAction.mockReset();
  proposeAction.mockReset();
});

describe("ChangesWorkspace — proposed", () => {
  it("offers approve and reject, not create pull request", () => {
    renderWorkspace([change({ status: "proposed" })]);

    expect(screen.getByRole("button", { name: /approve/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /reject/i })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /create pull request/i })).not.toBeInTheDocument();
  });
});

describe("ChangesWorkspace — approved", () => {
  it("offers create pull request instead of approve/reject", () => {
    renderWorkspace([change({ status: "approved" })]);

    expect(screen.getByRole("button", { name: /create pull request/i })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /^approve$/i })).not.toBeInTheDocument();
  });

  it("creating a pull request calls the action and renders the result", async () => {
    const user = userEvent.setup();
    executeAction.mockResolvedValue({
      ok: true,
      data: change({
        status: "pr_created",
        pr_number: 7,
        pr_url: "https://github.com/acme/widgets/pull/7",
        branch_name: "devpilot/change/abc123",
        commit_sha: "deadbee",
        execution_events: [
          { event: "execution_started", at: "t" },
          { event: "branch_created", at: "t" },
          { event: "patch_applied", at: "t" },
          { event: "commit_created", at: "t" },
          { event: "push_completed", at: "t" },
          { event: "pr_created", at: "t" },
        ],
      }),
    });

    renderWorkspace([change({ status: "approved" })]);
    await user.click(screen.getByRole("button", { name: /create pull request/i }));

    expect(executeAction).toHaveBeenCalledWith("change-1", "acme", "widgets");
    expect(await screen.findByText(/is open on GitHub/i)).toBeVisible();
    expect(screen.getByText("#7")).toBeVisible();
    expect(screen.getByRole("link", { name: /view on github/i })).toHaveAttribute(
      "href",
      "https://github.com/acme/widgets/pull/7",
    );
  });

  it("shows the action's error without touching the change", async () => {
    const user = userEvent.setup();
    executeAction.mockResolvedValue({
      ok: false,
      error: { code: "github_rate_limited", message: "GitHub's rate limit has been reached." },
    });

    renderWorkspace([change({ status: "approved" })]);
    await user.click(screen.getByRole("button", { name: /create pull request/i }));

    expect(await screen.findByText(/rate limit has been reached/i)).toBeVisible();
    expect(screen.getByRole("button", { name: /create pull request/i })).toBeEnabled();
  });
});

describe("ChangesWorkspace — committed", () => {
  it("offers a retry, distinct wording from a fresh approval", () => {
    renderWorkspace([
      change({
        status: "committed",
        branch_name: "devpilot/change/abc123",
        commit_sha: "deadbee",
        execution_error: "GitHub returned an unexpected status (502).",
        execution_events: [
          { event: "execution_started", at: "t" },
          { event: "branch_created", at: "t" },
          { event: "patch_applied", at: "t" },
          { event: "commit_created", at: "t" },
          { event: "push_completed", at: "t" },
        ],
      }),
    ]);

    expect(screen.getByRole("button", { name: /retry pull request/i })).toBeEnabled();
    expect(screen.getByText(/branch and commit already exist/i)).toBeVisible();
    expect(screen.getByText(/502/)).toBeVisible();
    expect(screen.getByText("devpilot/change/abc123")).toBeVisible();
  });
});

describe("ChangesWorkspace — pr_created", () => {
  it("shows the real PR link and no further actions", () => {
    renderWorkspace([
      change({
        status: "pr_created",
        pr_number: 12,
        pr_url: "https://github.com/acme/widgets/pull/12",
        branch_name: "devpilot/change/xyz789",
      }),
    ]);

    expect(screen.getByRole("link", { name: /view on github/i })).toHaveAttribute(
      "href",
      "https://github.com/acme/widgets/pull/12",
    );
    expect(screen.queryByRole("button", { name: /create pull request/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
    expect(screen.getByText(/this proposal is complete/i)).toBeVisible();
  });
});
