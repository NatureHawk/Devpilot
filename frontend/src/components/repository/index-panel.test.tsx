import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { IndexPanel, type IndexSummary } from "@/components/repository/index-panel";

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

function renderPanel(summary: IndexSummary) {
  return render(<IndexPanel repositoryId="repo-1" owner="acme" name="widgets" summary={summary} />);
}

beforeEach(() => {
  indexAction.mockReset();
});

describe("IndexPanel — not indexed", () => {
  it("offers indexing and explains what it does", () => {
    renderPanel(NOT_INDEXED);

    expect(screen.getByRole("heading", { name: /hasn't been indexed yet/i })).toBeVisible();
    expect(screen.getByText(/prepare its source for code-aware search/i)).toBeVisible();
    expect(screen.getByRole("button", { name: "Index repository" })).toBeEnabled();
  });

  it("runs the real indexing action for this repository", async () => {
    const user = userEvent.setup();
    indexAction.mockResolvedValue({
      ok: true,
      data: {
        filesIndexed: 5,
        filesParsed: 4,
        chunksCreated: 20,
        filesSkipped: 1,
        parseFailures: 0,
        complete: true,
        commitSha: "deadbeef",
      },
    });

    renderPanel(NOT_INDEXED);
    await user.click(screen.getByRole("button", { name: "Index repository" }));

    expect(indexAction).toHaveBeenCalledWith("repo-1", "acme", "widgets");
  });
});

describe("IndexPanel — indexing", () => {
  it("shows a pending state without inventing progress", async () => {
    const user = userEvent.setup();
    // Never resolves: holds the component in its in-flight state.
    indexAction.mockImplementation(() => new Promise(() => {}));

    renderPanel(NOT_INDEXED);
    await user.click(screen.getByRole("button", { name: "Index repository" }));

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /Indexing repository/i })).toBeVisible(),
    );
    // No fabricated percentage or file counter while the run is in flight.
    expect(screen.queryByText(/%/)).toBeNull();
  });
});

describe("IndexPanel — indexed", () => {
  it("reports the real statistics from the last run", () => {
    renderPanel(INDEXED);

    expect(screen.getByRole("heading", { name: "Repository indexed" })).toBeVisible();
    expect(screen.getByText("42")).toBeVisible();
    expect(screen.getByText("310")).toBeVisible();
    // Commits are shown short, the way git does.
    expect(screen.getByText("abcdef1")).toBeVisible();
  });

  it("offers re-indexing", () => {
    renderPanel(INDEXED);

    expect(screen.getByRole("button", { name: "Re-index" })).toBeEnabled();
  });

  it("shows the counts returned by a run that just completed", async () => {
    const user = userEvent.setup();
    indexAction.mockResolvedValue({
      ok: true,
      data: {
        filesIndexed: 7,
        filesParsed: 6,
        chunksCreated: 99,
        filesSkipped: 2,
        parseFailures: 0,
        complete: true,
        commitSha: "1234567890",
      },
    });

    renderPanel(NOT_INDEXED);
    await user.click(screen.getByRole("button", { name: "Index repository" }));

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Repository indexed" })).toBeVisible(),
    );
    expect(screen.getByText("99")).toBeVisible();
  });

  it("flags an index where some supported files failed to parse", async () => {
    const user = userEvent.setup();
    indexAction.mockResolvedValue({
      ok: true,
      data: {
        filesIndexed: 7,
        filesParsed: 5,
        chunksCreated: 40,
        filesSkipped: 0,
        parseFailures: 2,
        complete: false,
        commitSha: "abc1234",
      },
    });

    renderPanel(NOT_INDEXED);
    await user.click(screen.getByRole("button", { name: "Index repository" }));

    await waitFor(() => expect(screen.getByText("Partially parsed")).toBeVisible());
  });
});

describe("IndexPanel — failed", () => {
  it("shows the stored failure and offers a retry", () => {
    renderPanel({
      ...NOT_INDEXED,
      status: "failed",
      error: "GitHub's rate limit has been reached. Try again shortly.",
    });

    expect(screen.getByRole("heading", { name: "Indexing failed" })).toBeVisible();
    expect(screen.getByText(/rate limit has been reached/i)).toBeVisible();
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
