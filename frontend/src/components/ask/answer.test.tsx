import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Answer } from "@/components/ask/answer";
import { splitAnswer } from "@/components/ask/rich-text";
import type { AskSource } from "@/lib/ask-stream";

function source(label: string, overrides: Partial<AskSource> = {}): AskSource {
  return {
    label,
    chunk_id: `chunk-${label}`,
    file_path: "web/src/App.jsx",
    symbol: "Operations",
    chunk_type: "function",
    language: "jsx",
    start_line: 195,
    end_line: 253,
    score: 0.9,
    content: "const Operations = () => {}",
    ...overrides,
  };
}

function renderAnswer(text: string, sources: AskSource[], activeLabel: string | null = null) {
  const onSelect = vi.fn();
  render(
    <Answer text={text} sources={sources} onSelectSource={onSelect} activeLabel={activeLabel} />,
  );
  return onSelect;
}

describe("Answer — citations", () => {
  it("turns a citation into a chip naming the file, symbol and lines", async () => {
    const user = userEvent.setup();
    const onSelect = renderAnswer("Categories are memoised [S1].", [source("S1")]);

    const chip = screen.getByRole("button", { name: /Show source S1/ });
    expect(chip).toHaveTextContent("S1");
    expect(chip).toHaveTextContent("App.jsx · Operations · 195–253");
    await user.click(chip);
    expect(onSelect).toHaveBeenCalledWith("S1");
  });

  it("links every label in a grouped citation as its own chip", async () => {
    const user = userEvent.setup();
    const onSelect = renderAnswer("A stacked bar chart [S1, S2].", [
      source("S1"),
      source("S2", { symbol: "CATEGORY_COLORS", start_line: 191, end_line: 194 }),
    ]);

    expect(screen.getByRole("button", { name: /Show source S1/ })).toBeVisible();
    await user.click(screen.getByRole("button", { name: /Show source S2/ }));
    expect(onSelect).toHaveBeenCalledWith("S2");
    expect(screen.queryByText(/\[S1, S2\]/)).not.toBeInTheDocument();
  });

  it("drops a citation with no matching source instead of showing dead text", () => {
    renderAnswer("This is claimed [S7].", [source("S1")]);

    expect(screen.queryByRole("button", { name: /Show source S7/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/\[S7\]/)).not.toBeInTheDocument();
    expect(screen.getByText("This is claimed.")).toBeVisible();
  });

  it("keeps the real labels of a group and drops invented ones", () => {
    renderAnswer("Claimed [S1, S9].", [source("S1")]);

    expect(screen.getByRole("button", { name: /Show source S1/ })).toBeVisible();
    expect(screen.queryByText(/S9/)).not.toBeInTheDocument();
  });

  it("marks the active citation", () => {
    renderAnswer("Answer [S1].", [source("S1")], "S1");

    const chip = screen.getByRole("button", { name: /Show source S1/ });
    expect(chip).toHaveAttribute("aria-pressed", "true");
    expect(chip.className).toContain("border-accent");
  });
});

describe("Answer — formatting", () => {
  it("renders headings, lists, emphasis and inline code instead of raw markdown", () => {
    renderAnswer(
      "### What the code shows\n\n* **Operations** fetches data\n* Uses `useMemo`\n\n1. First\n2. Second",
      [],
    );

    expect(screen.getByRole("heading", { name: "What the code shows" })).toBeVisible();
    expect(screen.getAllByRole("list")).toHaveLength(2);
    expect(screen.getByText("Operations").tagName).toBe("STRONG");
    expect(screen.getByText("useMemo").tagName).toBe("CODE");
    expect(screen.queryByText(/###|\*\*/)).not.toBeInTheDocument();
  });

  it("does not read underscores inside identifiers as emphasis", () => {
    renderAnswer("It calls get_db_path before opening.", []);

    expect(screen.getByText("It calls get_db_path before opening.")).toBeVisible();
  });

  it("renders fenced code as a block", () => {
    const { container } = render(
      <Answer
        text={"```python\ndef get_db():\n    return conn\n```"}
        sources={[]}
        onSelectSource={vi.fn()}
        activeLabel={null}
      />,
    );

    expect(container.querySelector("pre")).toHaveTextContent("def get_db():");
  });

  it("renders answer text as data, not markup", () => {
    const { container } = render(
      <Answer
        text="<script>alert(1)</script>"
        sources={[]}
        onSelectSource={vi.fn()}
        activeLabel={null}
      />,
    );

    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText("<script>alert(1)</script>")).toBeVisible();
  });
});

describe("splitAnswer", () => {
  it("separates a closing 'What this means' section", () => {
    expect(splitAnswer("The answer.\n\n### What this means\nIt is memoised.")).toEqual({
      answer: "The answer.",
      meaning: "It is memoised.",
    });
    expect(splitAnswer("Answer\n\n**What this means**\nConclusion")).toEqual({
      answer: "Answer",
      meaning: "Conclusion",
    });
  });

  it("leaves an answer without that section whole", () => {
    expect(splitAnswer("Just an answer.")).toEqual({ answer: "Just an answer.", meaning: null });
  });
});
