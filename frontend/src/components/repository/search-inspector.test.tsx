import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SearchInspector } from "@/components/repository/search-inspector";
import type { SearchResponse } from "@/lib/api";

function response(overrides: Partial<SearchResponse> = {}): SearchResponse {
  return {
    model: "voyage-code-3",
    searched_chunks: 412,
    results: [
      {
        chunk_id: "c1",
        file_path: "backend/app/services/auth.py",
        language: "python",
        symbol: "authenticate",
        parent_symbol: "AuthService",
        chunk_type: "method",
        start_line: 42,
        end_line: 73,
        content: "def authenticate(self, token):\n    return verify(token)",
        score: 0.9123,
      },
      {
        chunk_id: "c2",
        file_path: "backend/app/api/deps.py",
        language: "python",
        symbol: "get_current_user",
        parent_symbol: null,
        chunk_type: "function",
        start_line: 10,
        end_line: 24,
        content: "def get_current_user(): ...",
        score: 0.8011,
      },
    ],
    ...overrides,
  };
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

  it("renders results in rank order with scores and line ranges", async () => {
    const user = userEvent.setup();
    const action = vi.fn().mockResolvedValue({ ok: true, data: response() });
    render(<SearchInspector action={action} />);

    await user.type(screen.getByLabelText("Search query"), "auth");
    await user.click(screen.getByRole("button", { name: /Search/ }));

    await waitFor(() => {
      expect(screen.getByText("backend/app/services/auth.py")).toBeVisible();
    });

    // Score visibility is the point of the inspector: it is how retrieval
    // quality gets judged before a model is involved.
    expect(screen.getByText("0.912")).toBeVisible();
    expect(screen.getByText("0.801")).toBeVisible();
    expect(screen.getByText(/lines 42–73/)).toBeVisible();
    // A method carries its class so the chunk reads correctly out of context.
    expect(screen.getByText("AuthService.authenticate")).toBeVisible();
    expect(screen.getByText(/2 of 412 chunks/)).toBeVisible();
  });

  it("passes the trimmed query to the action", async () => {
    const user = userEvent.setup();
    const action = vi.fn().mockResolvedValue({ ok: true, data: response() });
    render(<SearchInspector action={action} />);

    await user.type(screen.getByLabelText("Search query"), "  auth  ");
    await user.click(screen.getByRole("button", { name: /Search/ }));

    await waitFor(() => expect(action).toHaveBeenCalledWith("auth"));
  });

  it("hides source until a result is expanded", async () => {
    const user = userEvent.setup();
    const action = vi.fn().mockResolvedValue({ ok: true, data: response() });
    render(<SearchInspector action={action} />);

    await user.type(screen.getByLabelText("Search query"), "auth");
    await user.click(screen.getByRole("button", { name: /Search/ }));
    await waitFor(() => expect(screen.getByText(/lines 42–73/)).toBeVisible());

    expect(screen.queryByText(/return verify\(token\)/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { expanded: false, name: /auth\.py/ }));
    expect(screen.getByText(/return verify\(token\)/)).toBeVisible();
  });

  it("reports a failure without clearing the query", async () => {
    const user = userEvent.setup();
    const action = vi.fn().mockResolvedValue({
      ok: false,
      error: { code: "repository_not_indexed", message: "This repository has not been indexed." },
    });
    render(<SearchInspector action={action} />);

    await user.type(screen.getByLabelText("Search query"), "auth");
    await user.click(screen.getByRole("button", { name: /Search/ }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeVisible());
    expect(screen.getByText("This repository has not been indexed.")).toBeVisible();
    expect(screen.getByText("repository_not_indexed")).toBeVisible();
    expect(screen.getByLabelText("Search query")).toHaveValue("auth");
  });

  it("distinguishes no matches from no search", async () => {
    const user = userEvent.setup();
    const action = vi
      .fn()
      .mockResolvedValue({ ok: true, data: response({ results: [] }) });
    render(<SearchInspector action={action} />);

    await user.type(screen.getByLabelText("Search query"), "nothing");
    await user.click(screen.getByRole("button", { name: /Search/ }));

    await waitFor(() => expect(screen.getByText("No matches")).toBeVisible());
    expect(screen.queryByText(/Inspect what retrieval returns/)).not.toBeInTheDocument();
  });
});
