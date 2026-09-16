"use client";

import {
  ArrowRight,
  Compass,
  History,
  Loader2,
  type LucideIcon,
  MapPin,
  Plus,
  ShieldAlert,
  Square,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { loadConversationAction, refreshRepositoryAction } from "@/app/actions";
import { Composer, type ComposerHandle } from "@/components/ask/composer";
import { SourceViewer } from "@/components/ask/source-viewer";
import { TurnView, type Turn } from "@/components/ask/turn";
import { StageHeading } from "@/components/layout/stage-heading";
import { ButtonLink } from "@/components/ui/button";
import { NextStep } from "@/components/ui/next-step";
import type { Conversation } from "@/lib/api";
import { streamAsk, type AskSource } from "@/lib/ask-stream";
import { cn } from "@/lib/cn";
import { repositoryPath } from "@/lib/navigation";

/**
 * Starting points, grouped by what the user is trying to find out. They are
 * questions any codebase can answer — never claims about this one.
 */
const STARTING_POINTS: { title: string; icon: LucideIcon; questions: string[] }[] = [
  {
    title: "How it works",
    icon: Compass,
    questions: ["What happens when the app starts?", "How does data flow through the app?"],
  },
  {
    title: "Where things live",
    icon: MapPin,
    questions: ["Where are the API routes defined?", "Where is authentication handled?"],
  },
  {
    title: "What could go wrong",
    icon: ShieldAlert,
    questions: ["What could cause performance problems?", "How are errors handled?"],
  },
];

export type RecentConversation = Pick<Conversation, "id" | "title" | "created_at">;

type Active = { turn: number; label: string } | null;

function toTurns(conversation: Conversation): Turn[] {
  const turns: Turn[] = [];
  for (const message of conversation.messages) {
    if (message.role === "user") {
      turns.push({
        question: message.content,
        answer: "",
        sources: [],
        status: "done",
        historical: true,
      });
    } else if (message.role === "assistant") {
      const last = turns[turns.length - 1];
      if (!last) continue;
      last.answer = message.content;
      last.sources = message.sources.map((source): AskSource => ({
        label: source.label,
        chunk_id: source.chunk_id ?? source.label,
        file_path: source.file_path,
        symbol: source.symbol,
        chunk_type: "",
        language: source.language,
        start_line: source.start_line,
        end_line: source.end_line,
        score: source.score,
        content: "",
      }));
    }
  }
  return turns;
}

/**
 * The Ask screen.
 *
 * Before the first question it is a guided exploration: starting points
 * grouped by intent, with your own question as the alternative. Once a
 * conversation exists it becomes a compact, structured investigation with a
 * docked follow-up input.
 *
 * Progress comes from what actually happens — sources arrive, then text
 * streams. After each answer there is always a next step.
 */
export function ConversationPane({
  repositoryId,
  owner,
  name,
  disabledReason,
  blockedAction = null,
  recentConversations = [],
  initialConversationId = null,
}: {
  repositoryId: string | null;
  owner: string;
  name: string;
  disabledReason: string | null;
  blockedAction?: { label: string; href: string } | null;
  recentConversations?: RecentConversation[];
  initialConversationId?: string | null;
}) {
  const composer = useRef<ComposerHandle>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const abort = useRef<AbortController | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [streaming, setStreaming] = useState(false);
  const [active, setActive] = useState<Active>(null);
  const [showDetails, setShowDetails] = useState(false);
  // Arriving with ?conversation=… starts in the loading state rather than
  // setting it from an effect.
  const [loadingHistory, setLoadingHistory] = useState(Boolean(initialConversationId));
  const [historyError, setHistoryError] = useState<string | null>(null);

  const patchLast = useCallback((update: (turn: Turn) => Turn) => {
    setTurns((current) =>
      current.map((turn, index) => (index === current.length - 1 ? update(turn) : turn)),
    );
  }, []);

  const send = useCallback(
    async (question: string) => {
      if (!repositoryId || streaming || disabledReason) return;

      setTurns((current) => [
        ...current,
        { question, answer: "", sources: [], status: "retrieving" },
      ]);
      setActive(null);
      setStreaming(true);
      requestAnimationFrame(() =>
        scroller.current?.scrollTo?.({ top: scroller.current.scrollHeight, behavior: "smooth" }),
      );

      const controller = new AbortController();
      abort.current = controller;
      let completed = false;

      await streamAsk(
        repositoryId,
        conversationId ? { question, conversation_id: conversationId } : { question },
        {
          onSources: (sources, newConversationId) => {
            if (newConversationId) setConversationId(newConversationId);
            patchLast((turn) => ({ ...turn, sources, status: "generating" }));
          },
          onText: (delta) => patchLast((turn) => ({ ...turn, answer: turn.answer + delta })),
          onDone: () => {
            completed = true;
            patchLast((turn) => ({ ...turn, status: "done" }));
          },
          onError: (code, message) =>
            patchLast((turn) => ({ ...turn, status: "failed", error: { code, message } })),
        },
        controller.signal,
      );

      setStreaming(false);
      abort.current = null;
      // The answer was saved; the sidebar and recent questions should say so.
      if (completed) void refreshRepositoryAction(owner, name);
    },
    [repositoryId, streaming, disabledReason, conversationId, patchLast, owner, name],
  );

  const stop = useCallback(() => {
    abort.current?.abort();
    abort.current = null;
    setStreaming(false);
    // A stopped answer is kept: the text so far is real output.
    patchLast((turn) => (turn.status === "done" ? turn : { ...turn, status: "done" }));
  }, [patchLast]);

  const applyConversation = useCallback(
    (result: Awaited<ReturnType<typeof loadConversationAction>>) => {
      setLoadingHistory(false);
      if (result.ok) {
        setTurns(toTurns(result.data));
        setConversationId(result.data.id);
        setActive(null);
      } else {
        setHistoryError(result.error.message);
      }
    },
    [],
  );

  const openConversation = useCallback(
    async (id: string) => {
      if (!repositoryId) return;
      setLoadingHistory(true);
      setHistoryError(null);
      applyConversation(await loadConversationAction(repositoryId, id));
    },
    [repositoryId, applyConversation],
  );

  const startNew = () => {
    setTurns([]);
    setConversationId(undefined);
    setActive(null);
  };

  // The conversation named in the URL on arrival. State is only set once the
  // request returns, and a superseded request is ignored.
  useEffect(() => {
    if (!initialConversationId || !repositoryId) return;
    let cancelled = false;
    void loadConversationAction(repositoryId, initialConversationId).then((result) => {
      if (!cancelled) applyConversation(result);
    });
    return () => {
      cancelled = true;
    };
  }, [initialConversationId, repositoryId, applyConversation]);

  const activeSource =
    active !== null
      ? (turns[active.turn]?.sources.find((source) => source.label === active.label) ?? null)
      : null;
  const closeSource = useCallback(() => setActive(null), []);
  const lastQuestion = turns[turns.length - 1]?.question ?? "";
  const investigateHref =
    `${repositoryPath(owner, name, "changes")}?request=${encodeURIComponent(lastQuestion)}` +
    (conversationId ? `&conversation=${encodeURIComponent(conversationId)}` : "");
  const inConversation = turns.length > 0;

  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        {inConversation ? (
          <div className="border-line flex h-10 shrink-0 items-center gap-2 border-b px-4">
            <p className="text-ink-muted min-w-0 flex-1 truncate text-xs">
              <span className="text-ink font-medium">Understanding {name}</span>
              <span aria-hidden="true"> · </span>
              <span className="sr-only">, </span>
              {turns.length} question{turns.length === 1 ? "" : "s"}
            </p>
            {recentConversations.length > 0 ? (
              <HistoryMenu
                conversations={recentConversations}
                currentId={conversationId}
                onOpen={(id) => void openConversation(id)}
              />
            ) : null}
            <button
              type="button"
              onClick={startNew}
              disabled={streaming}
              className="text-ink-muted hover:bg-surface-hover hover:text-ink inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-xs transition-colors disabled:opacity-50"
            >
              <Plus aria-hidden="true" className="size-3.5" strokeWidth={2} />
              New conversation
            </button>
          </div>
        ) : null}

        <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto">
          {loadingHistory ? (
            <p role="status" className="text-ink-muted flex items-center gap-2 px-6 py-8 text-sm">
              <Loader2 aria-hidden="true" className="size-4 animate-spin" />
              Opening conversation…
            </p>
          ) : !inConversation ? (
            <GuidedStart
              name={name}
              disabledReason={disabledReason}
              blockedAction={blockedAction}
              recentConversations={recentConversations}
              historyError={historyError}
              onAsk={(question) => void send(question)}
              onOpen={(id) => void openConversation(id)}
              composer={
                <Composer
                  handleRef={composer}
                  variant="inline"
                  disabledReason={disabledReason}
                  onSubmit={(question) => void send(question)}
                  busy={streaming}
                  placeholder="What do you want to understand?"
                />
              }
            />
          ) : (
            <div className="mx-auto w-full max-w-3xl px-6 py-8">
              {turns.map((turn, index) => (
                <TurnView
                  key={index}
                  turn={turn}
                  index={index}
                  isLast={index === turns.length - 1}
                  activeLabel={active?.turn === index ? active.label : null}
                  onSelectSource={(label) =>
                    setActive((current) =>
                      current?.turn === index && current.label === label
                        ? null
                        : { turn: index, label },
                    )
                  }
                  onCloseSource={closeSource}
                  showDetails={showDetails}
                  onToggleDetails={() => setShowDetails((value) => !value)}
                  investigateHref={investigateHref}
                  onAskFollowUp={() => composer.current?.focus()}
                  onRetry={() => void send(turn.question)}
                />
              ))}
            </div>
          )}
        </div>

        {inConversation ? (
          <>
            {streaming ? (
              <div className="border-line bg-surface flex justify-center border-t py-2">
                <button
                  type="button"
                  onClick={stop}
                  className="border-line-strong text-ink-muted hover:text-ink inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-xs transition-colors"
                >
                  <Square aria-hidden="true" className="size-3" strokeWidth={2} />
                  Stop answering
                </button>
              </div>
            ) : null}
            <Composer
              handleRef={composer}
              disabledReason={disabledReason}
              onSubmit={(question) => void send(question)}
              busy={streaming}
              placeholder="Ask a follow-up…"
            />
          </>
        ) : null}
      </div>

      {activeSource ? (
        <SourceViewer source={activeSource} onClose={closeSource} variant="side" />
      ) : null}
    </div>
  );
}

function HistoryMenu({
  conversations,
  currentId,
  onOpen,
}: {
  conversations: RecentConversation[];
  currentId: string | undefined;
  onOpen: (id: string) => void;
}) {
  const menu = useRef<HTMLDetailsElement>(null);

  return (
    <details ref={menu} className="relative">
      <summary className="text-ink-muted hover:bg-surface-hover hover:text-ink inline-flex h-7 cursor-pointer list-none items-center gap-1.5 rounded-md px-2 text-xs transition-colors">
        <History aria-hidden="true" className="size-3.5" strokeWidth={2} />
        History
      </summary>
      <ul className="border-line bg-elevated animate-in absolute right-0 z-20 mt-1 max-h-80 w-80 overflow-y-auto rounded-lg border p-1 shadow-lg">
        {conversations.map((conversation) => (
          <li key={conversation.id}>
            <button
              type="button"
              onClick={() => {
                if (menu.current) menu.current.open = false;
                onOpen(conversation.id);
              }}
              className={cn(
                "hover:bg-surface-hover w-full rounded-md px-2.5 py-2 text-left transition-colors",
                currentId === conversation.id && "bg-surface-hover",
              )}
            >
              <span className="text-ink block truncate text-sm">
                {conversation.title ?? "Untitled conversation"}
              </span>
              <time
                dateTime={conversation.created_at}
                suppressHydrationWarning
                className="text-2xs text-ink-faint"
              >
                {new Date(conversation.created_at).toLocaleString()}
              </time>
            </button>
          </li>
        ))}
      </ul>
    </details>
  );
}

/**
 * Before the first question: teach the product by offering real questions to
 * start from, with your own question as the alternative rather than the page.
 */
function GuidedStart({
  name,
  disabledReason,
  blockedAction,
  recentConversations,
  historyError,
  onAsk,
  onOpen,
  composer,
}: {
  name: string;
  disabledReason: string | null;
  blockedAction: { label: string; href: string } | null;
  recentConversations: RecentConversation[];
  historyError: string | null;
  onAsk: (question: string) => void;
  onOpen: (id: string) => void;
  composer: React.ReactNode;
}) {
  const blocked = disabledReason !== null;

  return (
    <div className="mx-auto w-full max-w-3xl px-6 py-10">
      <StageHeading
        step="understand"
        title="What do you want to figure out?"
        description={`Pick a starting point, or ask your own question. DevPilot finds the relevant code in ${name}, explains it, and cites the exact files and lines it used.`}
      />

      {blocked ? (
        <NextStep
          className="mt-8"
          eyebrow="Before you can ask"
          title={blockedAction ? `${blockedAction.label} first` : "Asking isn't available yet"}
          description={disabledReason}
          action={
            blockedAction ? (
              <ButtonLink href={blockedAction.href} variant="primary" size="lg" forward>
                {blockedAction.label}
              </ButtonLink>
            ) : null
          }
        />
      ) : null}

      {historyError ? (
        <p role="alert" className="text-danger mt-6 text-sm">
          {historyError}
        </p>
      ) : null}

      <section aria-labelledby="starting-points" className="mt-10">
        <h2
          id="starting-points"
          className="text-ink-faint text-xs font-semibold tracking-[0.08em] uppercase"
        >
          Start with a question
        </h2>
        <div className="mt-4 grid gap-x-6 gap-y-6 sm:grid-cols-3">
          {STARTING_POINTS.map((group) => {
            const Icon = group.icon;
            const groupId = `starting-${group.title.toLowerCase().replace(/\W+/g, "-")}`;
            return (
              <div key={group.title} role="group" aria-labelledby={groupId}>
                <h3
                  id={groupId}
                  className="text-ink-muted flex items-center gap-1.5 text-xs font-medium"
                >
                  <Icon aria-hidden="true" className="text-accent size-3.5" strokeWidth={2} />
                  {group.title}
                </h3>
                <ul className="border-line mt-2 space-y-px border-t pt-1.5">
                  {group.questions.map((question) => (
                    <li key={question}>
                      <button
                        type="button"
                        disabled={blocked}
                        onClick={() => onAsk(question)}
                        className={cn(
                          "group -mx-2 flex w-[calc(100%+1rem)] items-start gap-2 rounded-md px-2 py-2 text-left transition-colors",
                          "hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent",
                        )}
                      >
                        <span className="text-ink min-w-0 flex-1 text-sm leading-snug">
                          {question}
                        </span>
                        <ArrowRight
                          aria-hidden="true"
                          className="text-ink-faint group-hover:text-accent mt-0.5 size-3.5 shrink-0 transition-[color,transform] group-hover:translate-x-0.5"
                          strokeWidth={2}
                        />
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      </section>

      <section aria-labelledby="own-question" className="mt-10">
        <div className="flex items-center gap-3">
          <h2
            id="own-question"
            className="text-ink-faint shrink-0 text-xs font-semibold tracking-[0.08em] uppercase"
          >
            Or ask your own question
          </h2>
          <span aria-hidden="true" className="bg-line h-px flex-1" />
        </div>
        <div className="mt-3">{composer}</div>
      </section>

      {recentConversations.length > 0 ? (
        <section aria-labelledby="recent-questions" className="mt-10">
          <h2
            id="recent-questions"
            className="text-ink-faint text-xs font-semibold tracking-[0.08em] uppercase"
          >
            Pick up a previous conversation
          </h2>
          <ul className="mt-2 space-y-px">
            {recentConversations.slice(0, 5).map((conversation) => (
              <li key={conversation.id}>
                <button
                  type="button"
                  onClick={() => onOpen(conversation.id)}
                  className="hover:bg-surface-hover text-ink-muted hover:text-ink -mx-2 block w-[calc(100%+1rem)] truncate rounded-md px-2 py-1.5 text-left text-sm transition-colors"
                >
                  {conversation.title ?? "Untitled conversation"}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
