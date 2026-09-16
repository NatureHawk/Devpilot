import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RepositoryList, type RepositoryEntry } from "@/components/repository/repository-list";
import type { ChangeStatus, IndexingStatus, Repository } from "@/lib/api";
import { deriveWorkflow } from "@/lib/workflow";

function entry(
  name: string,
  indexingStatus: IndexingStatus,
  conversationCount = 0,
  changeStatuses: ChangeStatus[] = [],
): RepositoryEntry {
  const repository: Repository = {
    id: `id-${name}`,
    provider: "github",
    owner: "acme",
    name,
    default_branch: "main",
    visibility: "public",
    indexing_status: indexingStatus,
    indexed_at: null,
    indexed_commit_sha: null,
    indexing_started_at: null,
    indexing_error: null,
    indexed_file_count: 0,
    indexed_parsed_file_count: 0,
    indexed_chunk_count: 0,
    created_at: "2026-09-01T00:00:00Z",
  };
  return {
    repository,
    workflow: deriveWorkflow({
      repositoryName: name,
      indexingStatus,
      conversationCount,
      changeStatuses,
    }),
  };
}

describe("RepositoryList", () => {
  it("leads with the first repository and its one primary next step", () => {
    render(
      <RepositoryList entries={[entry("RetailHub", "indexed"), entry("Chat", "not_indexed")]} />,
    );

    const lead = screen.getByRole("article", { name: "RetailHub" });
    expect(within(lead).getByRole("list", { name: "Repository progress" })).toHaveTextContent(
      /Connect.*\(done\).*Index.*\(done\).*Understand.*\(you are here\)/,
    );
    expect(within(lead).getByRole("link", { name: "Ask about this repository" })).toHaveAttribute(
      "href",
      "/repositories/acme/RetailHub/ask",
    );
  });

  it("shows other repositories as quiet rows: progress so far and the next step", () => {
    render(
      <RepositoryList
        entries={[
          entry("RetailHub", "indexed", 2),
          entry("Chat", "indexed"),
          entry("New", "not_indexed"),
        ]}
      />,
    );

    const others = screen.getByRole("region", { name: "Other repositories" });
    expect(others).toHaveTextContent("Connected");
    expect(others).toHaveTextContent("Ready to explore");
    expect(others).toHaveTextContent("Ready to index");

    const index = within(others).getByRole("link", { name: "Index repository — New" });
    expect(index).toHaveAttribute("href", "/repositories/acme/New");
    // Only the lead repository gets the page's primary action.
    expect(index.className).not.toContain("bg-accent");
  });

  it("flags a failed index as the step that needs attention", () => {
    render(<RepositoryList entries={[entry("Other", "indexed"), entry("Broken", "failed")]} />);

    const others = screen.getByRole("region", { name: "Other repositories" });
    expect(others).toHaveTextContent("Indexing failed");
    expect(within(others).getByRole("link", { name: "Retry indexing — Broken" })).toBeVisible();
  });
});
