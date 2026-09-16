import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { IndexPanel, type IndexSummary } from "@/components/repository/index-panel";
import type { NextAction } from "@/lib/workflow";

const indexAction = vi.hoisted(() => vi.fn());
vi.mock("@/app/actions", () => ({ indexRepositoryAction: indexAction }));

const NOT_INDEXED: IndexSummary = {
  status: "not_indexed",
  commitSha: null,
  indexedAt: null,
  fileCount: 0,
  parsedFileCount: 0,
  chunkCount: 0,
  error: null,
};

const INDEXED: IndexSummary = {
  status: "indexed",
  commitSha: "abcdef1234567890",
  indexedAt: "2026-08-25T10:00:00Z",
  fileCount: 42,
  parsedFileCount: 30,
  chunkCount: 310,
  error: null,
};

const OUTCOME = {
  filesIndexed: 7,
  filesParsed: 6,
  chunksCreated: 99,
  chunksEmbedded: 99,
  filesSkipped: 2,
  parseFailures: 0,
  complete: true,
  commitSha: "1234567890",
};

function renderPanel(summary: IndexSummary, next?: NextAction) {
  return render(
    <IndexPanel repositoryId="repo-1" owner="acme" name="widgets" summary={summary} next={next} />,
  );
}

beforeEach(() => {
  indexAction.mockReset();
});

describe("IndexPanel — not indexed", () => {
  it("makes indexing the one next step and says what it does", () => {
    renderPanel(NOT_INDEXED);

    expect(screen.getByRole("heading", { name: /hasn't been indexed yet/i })).toBeVisible();
    expect(screen.getByRole("region", { name: "Index widgets" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Index repository" })).toBeEnabled();
    expect(screen.getByText(/Nothing is written to GitHub/)).toBeVisible();
  });

  it("runs the real indexing action for this repository", async () => {
    const user = userEvent.setup();
    indexAction.mockResolvedValue({ ok: true, data: OUTCOME });

    renderPanel(NOT_INDEXED);
    await user.click(screen.getByRole("button", { name: "Index repository" }));

    expect(indexAction).toHaveBeenCalledWith("repo-1", "acme", "widgets");
  });
});

describe("IndexPanel — indexing", () => {
  it("explains what the run does without inventing progress", async () => {
    const user = userEvent.setup();
    // Never resolves: holds the component in its in-flight state.
    indexAction.mockImplementation(() => new Promise(() => {}));

    renderPanel(NOT_INDEXED);
    await user.click(screen.getByRole("button", { name: "Index repository" }));

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Indexing widgets" })).toBeVisible(),
    );
    const stages = screen.getByRole("list", { name: "Indexing" });
    expect(stages).toHaveTextContent("Generating embeddings for search");
    // Stages are shown running together — none is claimed done while the request is open.
    expect(screen.queryByText("(done)")).toBeNull();
    expect(screen.queryByText(/%/)).toBeNull();
    expect(screen.getByText(/Running for/)).toBeVisible();
  });
});

describe("IndexPanel — indexed", () => {
  it("summarises the real index and points to the workflow's next step", () => {
    renderPanel(INDEXED, {
      stage: "review",
      segment: "review",
      status: "Change awaiting review",
      title: "Review the proposed change",
      description: "Read the diff.",
      label: "Review diff",
    });

    expect(screen.getByRole("heading", { name: "widgets is indexed" })).toBeVisible();
    expect(screen.getByText("42")).toBeVisible();
    expect(screen.getByText("310")).toBeVisible();
    expect(screen.getByText("abcdef1")).toBeVisible();
    expect(screen.getByRole("link", { name: "Review diff" })).toHaveAttribute(
      "href",
      "/repositories/acme/widgets/review",
    );
  });

  it("keeps re-indexing available but quiet", () => {
    renderPanel(INDEXED);

    expect(screen.getByRole("button", { name: "Re-index" })).toBeEnabled();
  });

  it("after a successful run, says so and makes Ask the next step", async () => {
    const user = userEvent.setup();
    indexAction.mockResolvedValue({ ok: true, data: OUTCOME });

    renderPanel(NOT_INDEXED);
    await user.click(screen.getByRole("button", { name: "Index repository" }));

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "widgets is ready" })).toBeVisible(),
    );
    expect(screen.getByText("99")).toBeVisible();
    expect(screen.getByRole("link", { name: "Ask about this repository" })).toHaveAttribute(
      "href",
      "/repositories/acme/widgets/ask",
    );
  });

  it("flags an index where some supported files failed to parse", async () => {
    const user = userEvent.setup();
    indexAction.mockResolvedValue({
      ok: true,
      data: { ...OUTCOME, parseFailures: 2, complete: false },
    });

    renderPanel(NOT_INDEXED);
    await user.click(screen.getByRole("button", { name: "Index repository" }));

    await waitFor(() => expect(screen.getByText(/Partially parsed/)).toBeVisible());
  });
});

describe("IndexPanel — failed", () => {
  it("shows the stored failure and makes retrying the next step", () => {
    renderPanel({
      ...NOT_INDEXED,
      status: "failed",
      error: "GitHub's rate limit has been reached. Try again shortly.",
    });

    expect(screen.getByRole("heading", { name: "Indexing failed" })).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent(/rate limit has been reached/i);
    expect(screen.getByRole("button", { name: "Retry indexing" })).toBeEnabled();
  });

  it("surfaces a failure returned by the action", async () => {
    const user = userEvent.setup();
    indexAction.mockResolvedValue({
      ok: false,
      error: { code: "github_not_found", message: "The requested GitHub resource does not exist." },
    });

    renderPanel(NOT_INDEXED);
    await user.click(screen.getByRole("button", { name: "Index repository" }));

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Indexing failed" })).toBeVisible(),
    );
    expect(screen.getByText(/does not exist/i)).toBeVisible();
  });

  it("says a previous index is still intact after a failed run", () => {
    renderPanel({ ...INDEXED, status: "failed", error: "GitHub could not be reached." });

    expect(screen.getByText(/previous index is intact/i)).toBeVisible();
    expect(screen.getByText(/42 files and 310 chunks/i)).toBeVisible();
  });

  it("does not claim a previous index when there was never one", () => {
    renderPanel({ ...NOT_INDEXED, status: "failed", error: "GitHub could not be reached." });

    expect(screen.queryByText(/previous index is intact/i)).toBeNull();
  });
});
