import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConversationPane } from "@/components/ask/conversation-pane";

function renderPane(disabledReason: string | null) {
  return render(
    <ConversationPane
      repositoryId={disabledReason ? null : "repo-1"}
      disabledReason={disabledReason}
      onSourcesChange={vi.fn()}
      activeLabel={null}
      onSelectSource={vi.fn()}
    />,
  );
}

describe("ConversationPane", () => {
  it("cannot send while the repository is not ready, and says why", () => {
    renderPane("Connect this repository first.");

    expect(screen.getByRole("button", { name: "Send question" })).toBeDisabled();
    expect(screen.getByText("Connect this repository first.")).toBeInTheDocument();
  });

  it("fills the composer from an example prompt without producing an answer", async () => {
    const user = userEvent.setup();
    renderPane("Connect this repository first.");

    await user.click(screen.getByRole("button", { name: /Where is authentication handled/ }));

    expect(screen.getByRole("textbox")).toHaveValue("Where is authentication handled?");
    // Filling the composer must not create a conversation turn.
    expect(screen.getByRole("heading", { name: /Ask anything about this codebase/ })).toBeVisible();
  });

  it("keeps send disabled on an empty composer even when nothing else blocks it", async () => {
    const user = userEvent.setup();
    renderPane(null);

    const send = screen.getByRole("button", { name: "Send question" });
    expect(send).toBeDisabled();

    await user.type(screen.getByRole("textbox"), "How does routing work?");
    expect(send).toBeEnabled();
  });
});
