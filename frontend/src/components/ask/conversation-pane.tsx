"use client";

import { ArrowUpRight, Loader2, Square } from "lucide-react";
import { useCallback, useRef, useState } from "react";

import { Answer } from "@/components/ask/answer";
import { Composer, type ComposerHandle } from "@/components/ask/composer";
import { cn } from "@/lib/cn";
import { streamAsk, type AskSource } from "@/lib/ask-stream";

const EXAMPLE_PROMPTS = [
  "Where is authentication handled?",
  "How does a request move through the API?",
  "Which files are responsible for database access?",
  "Where should I start if I want to add rate limiting?",
] as const;

type Turn = {
  question: string;
  answer: string;
  sources: AskSource[];
  status: "retrieving" | "generating" | "done" | "failed";
  error?: { code: string; message: string };
};

/**
 * The conversation column.
 *
 * Progress is reported from what actually happened — sources arrive, then text
 * streams — rather than from a fabricated percentage. Nothing here simulates
 * work that is not occurring.
 */
export function ConversationPane({
  repositoryId,
  disabledReason,
  onSourcesChange,
  activeLabel,
  onSelectSource,
}: {
  repositoryId: string | null;
  disabledReason: string | null;
  onSourcesChange: (sources: AskSource[]) => void;
  activeLabel: string | null;
  onSelectSource: (label: string) => void;
}) {
  const composer = useRef<ComposerHandle>(null);
  const abort = useRef<AbortController | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [streaming, setStreaming] = useState(false);

  const patchLast = useCallback((update: (turn: Turn) => Turn) => {
    setTurns((current) =>
      current.map((turn, index) => (index === current.length - 1 ? update(turn) : turn)),
    );
  }, []);

  const send = useCallback(
    async (question: string) => {
      if (!repositoryId || streaming) return;

      setTurns((current) => [
        ...current,
        { question, answer: "", sources: [], status: "retrieving" },
      ]);
      setStreaming(true);

      const controller = new AbortController();
      abort.current = controller;

      await streamAsk(
        repositoryId,
        conversationId ? { question, conversation_id: conversationId } : { question },
        {
          onSources: (sources, newConversationId) => {
            if (newConversationId) setConversationId(newConversationId);
            onSourcesChange(sources);
            patchLast((turn) => ({ ...turn, sources, status: "generating" }));
          },
          onText: (delta) => patchLast((turn) => ({ ...turn, answer: turn.answer + delta })),
          onDone: () => patchLast((turn) => ({ ...turn, status: "done" })),
          onError: (code, message) =>
            patchLast((turn) => ({ ...turn, status: "failed", error: { code, message } })),
        },
        controller.signal,
      );

      setStreaming(false);
      abort.current = null;
    },
    [repositoryId, streaming, conversationId, onSourcesChange, patchLast],
  );

  const stop = useCallback(() => {
    abort.current?.abort();
    abort.current = null;
    setStreaming(false);
    // A stopped answer is kept, marked done: the text so far is real output,
    // and discarding it would throw away what the user already read.
    patchLast((turn) => (turn.status === "done" ? turn : { ...turn, status: "done" }));
  }, [patchLast]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        {turns.length === 0 ? (
          <FirstRun onPick={(prompt) => composer.current?.setValue(prompt)} />
        ) : (
          <div className="mx-auto w-full max-w-2xl px-6 py-8">
            {turns.map((turn, index) => (
              <article key={index} className="not-first:border-line not-first:mt-8 not-first:border-t not-first:pt-8">
                <h3 className="text-ink text-sm font-medium">{turn.question}</h3>

                {turn.status === "retrieving" ? (
                  <Status text="Retrieving relevant code…" />
                ) : null}

                {turn.sources.length > 0 && turn.status !== "retrieving" ? (
                  <p className="text-2xs text-ink-faint mt-2">
                    {turn.sources.length} source{turn.sources.length === 1 ? "" : "s"} retrieved
                  </p>
                ) : null}

                {turn.answer ? (
                  <div className="mt-3">
                    <Answer
                      text={turn.answer}
                      sources={turn.sources}
                      onSelectSource={onSelectSource}
                      activeLabel={activeLabel}
                    />
                  </div>
                ) : null}

                {turn.status === "generating" && !turn.answer ? (
                  <Status text="Generating answer…" />
                ) : null}

                {turn.error ? (
                  <div role="alert" className="border-line mt-3 rounded-md border px-3 py-2.5">
                    <p className="text-ink text-sm font-medium">Could not answer</p>
                    <p className="text-ink-muted mt-1 text-sm">{turn.error.message}</p>
                    <p className="text-2xs text-ink-faint mt-1.5 font-mono">{turn.error.code}</p>
                  </div>
                ) : null}
              </article>
            ))}
          </div>
        )}
      </div>

      {streaming ? (
        <div className="border-line bg-surface flex justify-center border-t py-2">
          <button
            type="button"
            onClick={stop}
            className="border-line-strong text-ink-muted hover:text-ink inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-xs transition-colors"
          >
            <Square aria-hidden="true" className="size-3" strokeWidth={2} />
            Stop
          </button>
        </div>
      ) : null}

      <Composer
        handleRef={composer}
        disabledReason={disabledReason}
        onSubmit={send}
        busy={streaming}
      />
    </div>
  );
}

function Status({ text }: { text: string }) {
  return (
    <p className="text-ink-muted mt-3 flex items-center gap-2 text-sm" aria-live="polite">
      <Loader2 aria-hidden="true" className="size-3.5 animate-spin" strokeWidth={2} />
      {text}
    </p>
  );
}

function FirstRun({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="mx-auto flex min-h-full w-full max-w-2xl flex-col justify-center px-6 py-12">
      <h2 className="text-ink text-2xl font-semibold tracking-tight">
        Ask anything about this codebase.
      </h2>
      <p className="text-ink-muted mt-2.5 text-sm leading-relaxed">
        DevPilot retrieves relevant code, explains how it fits together, and keeps answers grounded
        in the repository.
      </p>

      <ul className="divide-line border-line mt-8 divide-y border-y">
        {EXAMPLE_PROMPTS.map((prompt) => (
          <li key={prompt}>
            <button
              type="button"
              onClick={() => onPick(prompt)}
              className={cn(
                "group hover:bg-surface-hover -mx-2 flex w-[calc(100%+1rem)] items-center gap-3",
                "rounded px-2 py-2.5 text-left transition-colors",
              )}
            >
              <span className="text-ink-muted group-hover:text-ink min-w-0 flex-1 truncate text-sm">
                {prompt}
              </span>
              <ArrowUpRight
                aria-hidden="true"
                className="text-ink-faint size-3.5 shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
                strokeWidth={1.75}
              />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
