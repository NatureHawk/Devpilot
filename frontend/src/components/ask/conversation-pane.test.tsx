import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ConversationPane } from "@/components/ask/conversation-pane";

describe("ConversationPane", () => {
  it("cannot send while the repository is not ready, and says why", () => {
    render(<ConversationPane disabledReason="Connect this repository first." />);

    expect(screen.getByRole("button", { name: "Send question" })).toBeDisabled();
    expect(screen.getByText("Connect this repository first.")).toBeInTheDocument();
  });

  it("fills the composer from an example prompt without producing an answer", async () => {
    const user = userEvent.setup();
    render(<ConversationPane disabledReason="Connect this repository first." />);

    await user.click(screen.getByRole("button", { name: /Where is authentication handled/ }));

    expect(screen.getByRole("textbox")).toHaveValue("Where is authentication handled?");
    // Filling the composer must not create a conversation turn.
    expect(screen.getByRole("heading", { name: /Ask anything about this codebase/ })).toBeVisible();
  });

  it("keeps send disabled on an empty composer even when nothing else blocks it", async () => {
    const user = userEvent.setup();
    render(<ConversationPane disabledReason={null} />);

    const send = screen.getByRole("button", { name: "Send question" });
    expect(send).toBeDisabled();

    await user.type(screen.getByRole("textbox"), "How does routing work?");
    expect(send).toBeEnabled();
  });
});
