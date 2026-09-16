import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import { SearchInspector } from "@/components/repository/search-inspector";
import type { RetrievalCandidate, SearchResponse } from "@/lib/api";

function candidate(overrides: Partial<RetrievalCandidate> = {}): RetrievalCandidate {
  return {
    chunk_id: "c1",
    file_path: "backend/app/services/auth.py",
    language: "python",
    symbol: "authenticate",
    parent_symbol: "AuthService",
    chunk_type: "method",
    start_line: 42,
    end_line: 73,
    semantic_score: 0.9123,
    semantic_rank: 1,
    lexical_rank: null,
    lexical_score: 0,
    matched_terms: [],
    exact_match: null,
    final_rank: 1,
    low_value: false,
    ...overrides,
  };
}

function response(overrides: Partial<SearchResponse> = {}): SearchResponse {
  const auth = candidate();
  const deps = candidate({
    chunk_id: "c2",
    file_path: "backend/app/api/deps.py",
    symbol: "get_current_user",
    parent_symbol: null,
    chunk_type: "function",
    start_line: 10,
    end_line: 24,
    semantic_score: 0.8011,
    semantic_rank: 4,
    lexical_rank: 1,
    lexical_score: 1,
    matched_terms: ["getcurrentuser"],
    exact_match: "symbol",
    final_rank: 2,
  });
  const divider = candidate({
    chunk_id: "c3",
    file_path: "backend/app/api/deps.py",
    symbol: null,
    parent_symbol: null,
    chunk_type: "module",
    start_line: 1,
    end_line: 2,
    semantic_score: 0.95,
    semantic_rank: 2,
    final_rank: 9,
    low_value: true,
  });

  return {
    model: "gemini-embedding-001",
    searched_chunks: 412,
    strength: "useful",
    query_terms: ["auth"],
    lexical_available: true,
    context_chars: 1234,
    context_budget: 40000,
    max_sources: 12,
    semantic_candidates: [auth, divider, deps],
    lexical_candidates: [deps],
    results: [
      {
        ...auth,
        content: "def authenticate(self, token):\n    return verify(token)",
        score: 0.9123,
      },
      { ...deps, content: "def get_current_user(): ...", score: 0.8011 },
    ],
    ...overrides,
  };
}

type SearchAction = ComponentProps<typeof SearchInspector>["action"];

async function search(action: SearchAction, text = "auth") {
  const user = userEvent.setup();
  render(<SearchInspector action={action} />);
  await user.type(screen.getByLabelText("Search query"), text);
  await user.click(screen.getByRole("button", { name: /Search/ }));
  return user;
}

describe("SearchInspector", () => {
  it("shows an empty state before any query is run", () => {
    render(<SearchInspector action={vi.fn()} />);

    expect(screen.getByText(/Inspect what retrieval returns/)).toBeVisible();
    expect(screen.getByRole("button", { name: /Search/ })).toBeDisabled();
  });

  it("enables search once the query is non-empty", async () => {
    const user = userEvent.setup();
    render(<SearchInspector action={vi.fn()} />);

    await user.type(screen.getByLabelText("Search query"), "where is auth");
    expect(screen.getByRole("button", { name: /Search/ })).toBeEnabled();
  });

  it("will not submit a whitespace-only query", async () => {
    const user = userEvent.setup();
    const action = vi.fn();
    render(<SearchInspector action={action} />);

    await user.type(screen.getByLabelText("Search query"), "   ");
    expect(screen.getByRole("button", { name: /Search/ })).toBeDisabled();
    expect(action).not.toHaveBeenCalled();
  });

  it("renders selected sources in order with semantic scores and line ranges", async () => {
    await search(vi.fn().mockResolvedValue({ ok: true, data: response() }));

    const selected = await screen.findByRole("list", { name: "Selected sources" });
    const rows = within(selected).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByText("backend/app/services/auth.py")).toBeVisible();

    // Score visibility is the point of the inspector: it is how retrieval
    // quality gets judged before a model is involved.
    expect(within(rows[0]!).getByText("0.912")).toBeVisible();
    expect(within(rows[1]!).getByText("0.801")).toBeVisible();
    expect(within(rows[0]!).getByText(/lines 42–73/)).toBeVisible();
    // A method carries its class so the chunk reads correctly out of context.
    expect(within(rows[0]!).getByText("AuthService.authenticate")).toBeVisible();
  });

  it("marks how a source was matched", async () => {
    await search(vi.fn().mockResolvedValue({ ok: true, data: response() }));

    const selected = await screen.findByRole("list", { name: "Selected sources" });
    const second = within(selected).getAllByRole("listitem")[1]!;
    expect(within(second).getByText("symbol")).toBeVisible();
    expect(within(second).getByText("kw #1")).toBeVisible();
  });

  it("summarises budget, strength and candidate pools without a confidence number", async () => {
    await search(vi.fn().mockResolvedValue({ ok: true, data: response() }));

    await waitFor(() => expect(screen.getByText(/2 of 12 sources selected/)).toBeVisible());
    expect(screen.getByText(/1,234 \/ 40,000/)).toBeVisible();
    expect(screen.getByText("evidence: useful")).toBeVisible();
    expect(screen.getByText(/3 semantic · 1/)).toBeVisible();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it("keeps candidate lists collapsed and shows selection and trivia inside them", async () => {
    const user = await search(vi.fn().mockResolvedValue({ ok: true, data: response() }));

    const summary = await screen.findByText("Semantic candidates (3)");
    expect(screen.getByText("Keyword candidates (1)")).toBeVisible();

    await user.click(summary);
    const table = summary.closest("details")!;
    expect(within(table).getAllByText("selected")).toHaveLength(2);
    expect(within(table).getByText("trivia")).toBeVisible();
  });

  it("says when keyword search needs a re-index", async () => {
    await search(
      vi.fn().mockResolvedValue({ ok: true, data: response({ lexical_available: false }) }),
    );

    await waitFor(() => expect(screen.getByText(/re-index to enable it/)).toBeVisible());
  });

  it("passes the trimmed query to the action", async () => {
    const action = vi.fn().mockResolvedValue({ ok: true, data: response() });
    await search(action, "  auth  ");

    await waitFor(() => expect(action).toHaveBeenCalledWith("auth"));
  });

  it("hides source until a result is expanded", async () => {
    const user = await search(vi.fn().mockResolvedValue({ ok: true, data: response() }));
    await waitFor(() => expect(screen.getByText(/lines 42–73/)).toBeVisible());

    expect(screen.queryByText(/return verify\(token\)/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { expanded: false, name: /auth\.py/ }));
    expect(screen.getByText(/return verify\(token\)/)).toBeVisible();
  });

  it("reports a failure without clearing the query", async () => {
    await search(
      vi.fn().mockResolvedValue({
        ok: false,
        error: { code: "repository_not_indexed", message: "This repository has not been indexed." },
      }),
    );

    await waitFor(() => expect(screen.getByRole("alert")).toBeVisible());
    expect(screen.getByText("This repository has not been indexed.")).toBeVisible();
    expect(screen.getByText("repository_not_indexed")).toBeVisible();
    expect(screen.getByLabelText("Search query")).toHaveValue("auth");
  });

  it("distinguishes no matches from no search", async () => {
    await search(
      vi.fn().mockResolvedValue({
        ok: true,
        data: response({ results: [], semantic_candidates: [], lexical_candidates: [] }),
      }),
      "nothing",
    );

    await waitFor(() => expect(screen.getByText("No matches")).toBeVisible());
    expect(screen.queryByText(/Inspect what retrieval returns/)).not.toBeInTheDocument();
  });
});
