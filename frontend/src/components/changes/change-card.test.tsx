import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChangeCard } from "@/components/changes/change-card";
import type { ProposedChange } from "@/lib/api";

const executeAction = vi.hoisted(() => vi.fn());
const reviewAction = vi.hoisted(() => vi.fn());
vi.mock("@/app/actions", () => ({
  executeChangeAction: executeAction,
  reviewChangeAction: reviewAction,
}));

const DIFF = [
  "--- a/backend/api.py",
  "+++ b/backend/api.py",
  "@@ -1,2 +1,3 @@",
  " import os",
  "-x = 1",
  "+x = 2",
  "+y = 3",
].join("\n");

function change(overrides: Partial<ProposedChange> = {}): ProposedChange {
  return {
    id: "change-1",
    repository_id: "repo-1",
    conversation_id: null,
    status: "proposed",
    request: "Add input validation",
    summary: "Validates the registration payload before use.",
    diff: DIFF,
    files_changed: 1,
    tool_calls_used: 3,
    indexed_commit_sha: "abc1234",
    model: "gemini-3.6-flash",
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

function renderCard(value: ProposedChange) {
  const onChange = vi.fn();
  render(<ChangeCard change={value} owner="acme" name="widgets" onChange={onChange} />);
  return onChange;
}

beforeEach(() => {
  executeAction.mockReset();
  reviewAction.mockReset();
});

describe("ChangeCard — proposed", () => {
  it("lays out the request, findings, files and the diff, with review as the current step", () => {
    renderCard(change());

    expect(screen.getByRole("heading", { name: "Add input validation" })).toBeVisible();
    expect(screen.getByText(/Validates the registration payload/)).toBeVisible();
    // Summarised under "Files affected" and again in the diff's own file header.
    expect(screen.getByText("backend/api.py", { selector: "span" })).toBeVisible();
    expect(screen.getAllByText("+2")).toHaveLength(2);
    expect(screen.getByRole("list", { name: "Change progress" })).toHaveTextContent(
      "Reviewed(current step)",
    );
  });

  it("offers approve as the primary action and no pull request yet", () => {
    renderCard(change());

    expect(screen.getByRole("button", { name: /approve change/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /^reject$/i })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /create branch & pr/i })).not.toBeInTheDocument();
  });

  it("asks for confirmation before rejecting", async () => {
    const user = userEvent.setup();
    reviewAction.mockResolvedValue({ ok: true, data: change({ status: "rejected" }) });
    const onChange = renderCard(change());

    await user.click(screen.getByRole("button", { name: /^reject$/i }));
    expect(reviewAction).not.toHaveBeenCalled();
    expect(screen.getByText(/can't be approved later/)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Reject change" }));
    expect(reviewAction).toHaveBeenCalledWith("change-1", "reject", "acme", "widgets");
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ status: "rejected" }));
  });

  it("records an approval through the action", async () => {
    const user = userEvent.setup();
    reviewAction.mockResolvedValue({ ok: true, data: change({ status: "approved" }) });
    const onChange = renderCard(change());

    await user.click(screen.getByRole("button", { name: /approve change/i }));

    expect(reviewAction).toHaveBeenCalledWith("change-1", "approve", "acme", "widgets");
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ status: "approved" }));
    expect(executeAction).not.toHaveBeenCalled();
  });

  it("shows the grounded investigation: root cause, cited evidence and anchor locations", () => {
    renderCard(
      change({
        report: {
          outcome: "change_proposed",
          root_cause: "The payload is used before it is checked [S2].",
          expected_behavior: "Invalid payloads are rejected with 422.",
          confidence: "medium",
          evidence: [
            {
              id: "S2",
              path: "backend/api.py",
              start_line: 10,
              end_line: 24,
              symbol: "register",
              origin: "read_file",
              claim: "register() writes the payload directly.",
            },
          ],
          proposed_changes: [
            { path: "backend/api.py", start_line: 2, end_line: 2, reason: "Validate first." },
          ],
        },
      }),
    );

    expect(screen.getByText(/The payload is used before it is checked/)).toBeVisible();
    expect(screen.getByText(/Invalid payloads are rejected with 422/)).toBeVisible();
    expect(screen.getByText(/medium confidence/)).toBeVisible();
    expect(screen.getByText("S2 · backend/api.py:10–24")).toBeVisible();
    expect(screen.getByText(/register\(\) writes the payload directly/)).toBeVisible();
    expect(screen.getByText("backend/api.py:2")).toBeVisible();
    expect(screen.getByRole("link", { name: "Review diff →" })).toHaveAttribute(
      "href",
      "#change-change-1-diff",
    );
  });
});

describe("ChangeCard — approved", () => {
  it("makes Create branch & PR the next action", () => {
    renderCard(change({ status: "approved" }));

    expect(screen.getByRole("button", { name: /create branch & pr/i })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /approve change/i })).not.toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Change progress" })).toHaveTextContent(
      "Shipped(current step)",
    );
  });

  it("creates the pull request through the action", async () => {
    const user = userEvent.setup();
    executeAction.mockResolvedValue({ ok: true, data: change({ status: "pr_created" }) });
    const onChange = renderCard(change({ status: "approved" }));

    await user.click(screen.getByRole("button", { name: /create branch & pr/i }));

    expect(executeAction).toHaveBeenCalledWith("change-1", "acme", "widgets");
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ status: "pr_created" }));
  });

  it("shows the action's error and keeps the action available", async () => {
    const user = userEvent.setup();
    executeAction.mockResolvedValue({
      ok: false,
      error: { code: "github_rate_limited", message: "GitHub's rate limit has been reached." },
    });
    renderCard(change({ status: "approved" }));

    await user.click(screen.getByRole("button", { name: /create branch & pr/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/rate limit has been reached/i);
    expect(screen.getByRole("button", { name: /create branch & pr/i })).toBeEnabled();
  });
});

describe("ChangeCard — committed", () => {
  it("offers a retry with its own explanation and real progress", () => {
    renderCard(
      change({
        status: "committed",
        branch_name: "devpilot/change/abc123",
        execution_error: "GitHub returned an unexpected status (502).",
        execution_events: [
          { event: "execution_started", at: "t" },
          { event: "branch_created", at: "t" },
          { event: "patch_applied", at: "t" },
          { event: "commit_created", at: "t" },
          { event: "push_completed", at: "t" },
        ],
      }),
    );

    expect(screen.getByRole("button", { name: /retry pull request/i })).toBeEnabled();
    expect(screen.getByText(/branch and commit already exist/i)).toBeVisible();
    expect(screen.getByText(/502/)).toBeVisible();
    expect(screen.getByText("devpilot/change/abc123")).toBeVisible();
    const progress = screen.getByRole("list", { name: "Pull request progress" });
    expect(within(progress).getByText("Pushed").nextSibling).toHaveTextContent("(done)");
    expect(within(progress).getByText("Pull request opened").nextSibling).toHaveTextContent(
      "(not started)",
    );
  });
});

describe("ChangeCard — shipped and closed", () => {
  it("links to the real pull request and offers nothing else", () => {
    renderCard(
      change({
        status: "pr_created",
        pr_number: 12,
        pr_url: "https://github.com/acme/widgets/pull/12",
      }),
    );

    expect(screen.getByRole("link", { name: /view pull request #12/i })).toHaveAttribute(
      "href",
      "https://github.com/acme/widgets/pull/12",
    );
    expect(
      screen.queryByRole("button", { name: /approve|create branch/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/Merging happens on GitHub/)).toBeVisible();
  });

  it("lets a stuck execution be retried instead of stranding it on a spinner", async () => {
    // If the process handling an execution dies, the row stays "executing".
    // The backend refuses a genuine in-flight run and reclaims an abandoned
    // one, so the button has to stay available.
    const user = userEvent.setup();
    executeAction.mockResolvedValue({
      ok: true,
      data: change({ status: "pr_created", pr_number: 4, pr_url: "https://x/pull/4" }),
    });
    const onChange = renderCard(change({ status: "executing" }));

    const button = screen.getByRole("button", { name: /creating pull request/i });
    expect(button).toBeEnabled();
    await user.click(button);

    expect(executeAction).toHaveBeenCalledWith("change-1", "acme", "widgets");
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ status: "pr_created" }));
  });

  it("sends a stale proposal back to investigation with the same request", () => {
    renderCard(change({ status: "stale" }));

    expect(screen.getByText("This proposal is out of date")).toBeVisible();
    expect(screen.getByRole("link", { name: "Investigate again" })).toHaveAttribute(
      "href",
      "/repositories/acme/widgets/changes?request=Add%20input%20validation",
    );
  });
});
