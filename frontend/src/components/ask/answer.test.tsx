import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Answer } from "@/components/ask/answer";
import type { AskSource } from "@/lib/ask-stream";

function source(label: string): AskSource {
  return {
    label,
    chunk_id: `chunk-${label}`,
    file_path: "app/auth.py",
    symbol: "authenticate",
    chunk_type: "function",
    language: "python",
    start_line: 10,
    end_line: 20,
    score: 0.9,
    content: "def authenticate(): ...",
  };
}

describe("Answer", () => {
  it("turns a citation into a control that selects its source", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();

    render(
      <Answer
        text="Sessions are created in the auth service [S1]."
        sources={[source("S1")]}
        onSelectSource={onSelect}
        activeLabel={null}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Show source S1" }));
    expect(onSelect).toHaveBeenCalledWith("S1");
  });

  it("keeps the surrounding prose intact", () => {
    render(
      <Answer
        text="Sessions are created in the auth service [S1]."
        sources={[source("S1")]}
        onSelectSource={vi.fn()}
        activeLabel={null}
      />,
    );

    expect(screen.getByText(/Sessions are created in the auth service/)).toBeVisible();
  });

  it("leaves a citation with no matching source as plain text", () => {
    /* A label the model invented must not become a link to nothing. */
    render(
      <Answer
        text="This is claimed [S7]."
        sources={[source("S1")]}
        onSelectSource={vi.fn()}
        activeLabel={null}
      />,
    );

    expect(screen.queryByRole("button", { name: "Show source S7" })).not.toBeInTheDocument();
    expect(screen.getByText(/\[S7\]/)).toBeVisible();
  });

  it("marks the active citation", () => {
    render(
      <Answer
        text="Answer [S1]."
        sources={[source("S1")]}
        onSelectSource={vi.fn()}
        activeLabel="S1"
      />,
    );

    expect(screen.getByRole("button", { name: "Show source S1" }).className).toContain(
      "border-accent",
    );
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

  it("handles multiple citations on one line", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();

    render(
      <Answer
        text="Both [S1] and [S2] matter."
        sources={[source("S1"), source("S2")]}
        onSelectSource={onSelect}
        activeLabel={null}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Show source S2" }));
    expect(onSelect).toHaveBeenCalledWith("S2");
  });
});
