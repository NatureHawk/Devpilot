import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DiffView } from "@/components/changes/diff-view";

/** Diff lines carry meaningful leading whitespace, which the default text
    normalizer would collapse away. */
const exact = { normalizer: (text: string) => text };

const DIFF = [
  "--- a/backend/app/auth.py",
  "+++ b/backend/app/auth.py",
  "@@ -10,6 +10,7 @@",
  " def register(data):",
  "-    user = create_user(data)",
  "+    validated = schema.parse(data)",
  "+    user = create_user(validated)",
  "     return user",
].join("\n");

describe("DiffView", () => {
  it("renders the file path as a heading", () => {
    render(<DiffView diff={DIFF} />);
    expect(screen.getByText("backend/app/auth.py")).toBeVisible();
  });

  it("counts additions and removals", () => {
    render(<DiffView diff={DIFF} />);
    expect(screen.getByText("+2")).toBeVisible();
    expect(screen.getByText("−1")).toBeVisible();
  });

  it("shows added and removed source lines", () => {
    render(<DiffView diff={DIFF} />);
    expect(screen.getByText("+    validated = schema.parse(data)", exact)).toBeVisible();
    expect(screen.getByText("-    user = create_user(data)", exact)).toBeVisible();
  });

  it("numbers lines from the hunk header", () => {
    render(<DiffView diff={DIFF} />);

    // Context line at old 10 / new 10, per "@@ -10,6 +10,7 @@".
    const contextRow = screen.getByText(" def register(data):", exact).closest("tr");
    expect(contextRow).not.toBeNull();
    const cells = within(contextRow as HTMLElement).getAllByRole("cell");
    expect(cells[0]).toHaveTextContent("10");
    expect(cells[1]).toHaveTextContent("10");
  });

  it("leaves the old-side number blank on an added line", () => {
    render(<DiffView diff={DIFF} />);

    const addedRow = screen
      .getByText("+    validated = schema.parse(data)", exact)
      .closest("tr") as HTMLElement;
    const cells = within(addedRow).getAllByRole("cell");
    expect(cells[0]).toHaveTextContent("");
    expect(cells[1]).not.toHaveTextContent("");
  });

  it("splits a multi-file diff into separate sections", () => {
    const twoFiles = `${DIFF}\n--- a/second.py\n+++ b/second.py\n@@ -1,2 +1,2 @@\n-old\n+new`;
    render(<DiffView diff={twoFiles} />);

    expect(screen.getByText("backend/app/auth.py")).toBeVisible();
    expect(screen.getByText("second.py")).toBeVisible();
  });

  it("renders diff text as data, not markup", () => {
    /* Repository source is untrusted; a script tag must appear as text. */
    const hostile = [
      "--- a/x.html",
      "+++ b/x.html",
      "@@ -1,1 +1,1 @@",
      "+<script>alert(1)</script>",
    ].join("\n");

    const { container } = render(<DiffView diff={hostile} />);

    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText("+<script>alert(1)</script>")).toBeVisible();
  });

  it("says so when there is nothing to show", () => {
    render(<DiffView diff="" />);
    expect(screen.getByText(/contains no changes/)).toBeVisible();
  });
});
