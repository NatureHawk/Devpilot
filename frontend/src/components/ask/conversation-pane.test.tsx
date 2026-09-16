import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConversationPane } from "@/components/ask/conversation-pane";
import type { AskSource, AskStreamHandlers } from "@/lib/ask-stream";

const streamAsk = vi.hoisted(() => vi.fn());
const refreshAction = vi.hoisted(() => vi.fn());
const loadConversation = vi.hoisted(() => vi.fn());

vi.mock("@/lib/ask-stream", () => ({ streamAsk }));
vi.mock("@/app/actions", () => ({
  refreshRepositoryAction: refreshAction,
  loadConversationAction: loadConversation,
}));

const SOURCES: AskSource[] = [
  {
    label: "S1",
    chunk_id: "c1",
    file_path: "web/src/App.jsx",
    symbol: "Operations",
    chunk_type: "function",
    language: "jsx",
    start_line: 195,
    end_line: 197,
    score: 0.66,
    content: "const Operations = () => {\n  const categories = useMemo(() => keys, [data]);\n};",
  },
];

function renderPane(disabledReason: string | null = null) {
  return render(
    <ConversationPane
      repositoryId={disabledReason ? null : "repo-1"}
      owner="acme"
      name="widgets"
      disabledReason={disabledReason}
      blockedAction={
        disabledReason ? { label: "Index repository", href: "/repositories/acme/widgets" } : null
      }
      recentConversations={[]}
    />,
  );
}

/** Drives the stream the way the API does: sources, text, done. */
function answerWith(text: string, sources = SOURCES) {
  streamAsk.mockImplementation(
    async (_repo: string, _body: unknown, handlers: AskStreamHandlers) => {
      handlers.onSources(sources, "conv-9");
      handlers.onText(text);
      handlers.onDone("conv-9", "msg-1");
    },
  );
}

async function ask(question: string) {
  const user = userEvent.setup();
  await user.type(screen.getByRole("textbox"), question);
  await user.click(screen.getByRole("button", { name: "Ask" }));
  return user;
}

/** A never-resolving stream, for inspecting the in-progress state. */
function answerLater() {
  streamAsk.mockImplementation(() => new Promise<void>(() => {}));
}

beforeEach(() => {
  streamAsk.mockReset();
  refreshAction.mockReset();
  loadConversation.mockReset();
});

describe("ConversationPane — before a question", () => {
  it("is a guided exploration, not an empty chat", () => {
    renderPane();

    expect(screen.getByRole("heading", { name: "What do you want to figure out?" })).toBeVisible();
    expect(screen.getByText(/Understand/)).toBeVisible();
    for (const group of ["How it works", "Where things live", "What could go wrong"]) {
      expect(screen.getByRole("group", { name: group })).toBeVisible();
    }
    expect(screen.getByRole("heading", { name: "Or ask your own question" })).toBeVisible();
  });

  it("explains what blocks asking and links to the step that resolves it", () => {
    renderPane("DevPilot answers from the indexed code, so this repository needs indexing first.");

    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Where are the API routes defined/ })).toBeDisabled();
    expect(screen.getByRole("link", { name: "Index repository" })).toHaveAttribute(
      "href",
      "/repositories/acme/widgets",
    );
  });

  it("asks a starting-point question in one click", async () => {
    const user = userEvent.setup();
    answerLater();
    renderPane();

    await user.click(screen.getByRole("button", { name: /Where are the API routes defined/ }));

    expect(streamAsk).toHaveBeenCalledWith(
      "repo-1",
      { question: "Where are the API routes defined?" },
      expect.any(Object),
      expect.any(AbortSignal),
    );
    expect(
      await screen.findByRole("heading", { name: "Where are the API routes defined?" }),
    ).toBeVisible();
    // The guided start gives way to the conversation.
    expect(screen.queryByRole("heading", { name: "What do you want to figure out?" })).toBeNull();
  });

  it("switches to a compact conversation with a follow-up input once asked", async () => {
    answerLater();
    renderPane();
    await ask("How does routing work?");

    expect(await screen.findByText("Understanding widgets")).toBeVisible();
    expect(screen.getByRole("textbox")).toHaveAttribute("placeholder", "Ask a follow-up…");
    expect(screen.getByRole("button", { name: "New conversation" })).toBeVisible();
  });

  it("keeps Ask disabled on an empty composer", async () => {
    const user = userEvent.setup();
    renderPane();

    const button = screen.getByRole("button", { name: "Ask" });
    expect(button).toBeDisabled();
    await user.type(screen.getByRole("textbox"), "How does routing work?");
    expect(button).toBeEnabled();
  });
});

describe("ConversationPane — progress", () => {
  it("shows the real stages while an answer is on its way", async () => {
    let handlers: AskStreamHandlers | undefined;
    streamAsk.mockImplementation(
      (_repo: string, _body: unknown, next: AskStreamHandlers) =>
        new Promise<void>(() => {
          handlers = next;
        }),
    );
    renderPane();
    await ask("Where is useMemo used?");

    const progress = await screen.findByRole("list", { name: "Answer progress" });
    expect(progress).toHaveTextContent("Finding the relevant code");

    handlers?.onSources(SOURCES, "conv-9");
    await waitFor(() => expect(progress).toHaveTextContent("Found 1 relevant source"));
    expect(progress).toHaveTextContent("Writing the answer");
  });
});

describe("ConversationPane — the result", () => {
  it("structures the result as question, answer, evidence and a next step", async () => {
    answerWith(
      "Categories are memoised in Operations [S1].\n\n### What this means\nIt recomputes only when data changes.",
    );
    renderPane();
    await ask("Where is useMemo used?");

    expect(await screen.findByRole("heading", { name: "Where is useMemo used?" })).toBeVisible();
    expect(screen.getByRole("region", { name: "Answer" })).toHaveTextContent(
      "Categories are memoised",
    );
    const evidence = screen.getByRole("region", { name: "What DevPilot found" });
    expect(within(evidence).getByText("1 relevant source · 1 file")).toBeVisible();
    expect(screen.getByRole("region", { name: "What this means" })).toHaveTextContent(
      "It recomputes only when data changes.",
    );

    const next = screen.getByRole("region", { name: "Ready to change something here?" });
    expect(within(next).getByRole("button", { name: "Ask another question" })).toBeVisible();
    expect(within(next).getByRole("link", { name: "Investigate this code" })).toHaveAttribute(
      "href",
      "/repositories/acme/widgets/changes?request=Where%20is%20useMemo%20used%3F&conversation=conv-9",
    );
    expect(refreshAction).toHaveBeenCalledWith("acme", "widgets");
  });

  it("opens the cited code with its real line numbers", async () => {
    answerWith("Memoised [S1].");
    renderPane();
    const user = await ask("Where is useMemo used?");

    await user.click(await screen.findByRole("button", { name: /Show source S1/ }));

    const viewers = screen.getAllByRole("complementary", { name: "Operations" });
    expect(viewers.length).toBeGreaterThan(0);
    const viewer = viewers[0]!;
    expect(viewer).toHaveTextContent("web/src/App.jsx");
    expect(within(viewer).getByText("196")).toBeVisible();
    expect(viewer).toHaveTextContent("useMemo(() => keys, [data])");

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("complementary", { name: "Operations" })).not.toBeInTheDocument();
  });

  it("lists evidence as compact rows that open the code", async () => {
    answerWith("Memoised [S1].");
    renderPane();
    const user = await ask("Where is useMemo used?");

    const evidence = await screen.findByRole("region", { name: "What DevPilot found" });
    const row = within(evidence).getByRole("listitem");
    expect(row).toHaveTextContent("Operations");
    expect(row).toHaveTextContent("web/src/App.jsx · lines 195–197");
    expect(row).toHaveTextContent("Cited");

    await user.click(within(row).getByRole("button", { name: /View code/ }));
    expect(screen.getAllByRole("complementary", { name: "Operations" }).length).toBeGreaterThan(0);
  });

  it("keeps retrieval diagnostics behind a toggle", async () => {
    answerWith("Memoised [S1].");
    renderPane();
    const user = await ask("Where is useMemo used?");

    await screen.findByRole("region", { name: "What DevPilot found" });
    const toggle = screen.getByRole("button", { name: "Retrieval diagnostics" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/semantic 0\.660/)).not.toBeInTheDocument();

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/semantic 0\.660/)).toBeVisible();
  });

  it("suggests a more specific question when nothing matched", async () => {
    answerWith("The repository evidence does not cover this.", []);
    renderPane();
    await ask("Where is Stripe webhook processing?");

    expect(
      await screen.findByRole("region", { name: "Try a more specific question" }),
    ).toBeVisible();
    expect(screen.queryByRole("link", { name: "Investigate this code" })).not.toBeInTheDocument();
  });

  it("offers to try again after a failure", async () => {
    streamAsk.mockImplementation(async (_r: string, _b: unknown, handlers: AskStreamHandlers) => {
      handlers.onError("llm_rate_limited", "The language model is rate limited.");
    });
    renderPane();
    await ask("Where is useMemo used?");

    expect(await screen.findByRole("alert")).toHaveTextContent("rate limited");
    expect(screen.getByRole("button", { name: "Ask again" })).toBeVisible();
    expect(refreshAction).not.toHaveBeenCalled();
  });
});
