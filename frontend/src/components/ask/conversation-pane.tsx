"use client";

import { ArrowUpRight } from "lucide-react";
import { useRef } from "react";

import { Composer, type ComposerHandle } from "@/components/ask/composer";

const EXAMPLE_PROMPTS = [
  "Where is authentication handled?",
  "How does a request move through the API?",
  "Which files are responsible for database access?",
  "Where should I start if I want to add rate limiting?",
] as const;

/**
 * First-run state of the conversation column.
 *
 * The examples are illustrative of the kind of question DevPilot is built for;
 * selecting one fills the composer and nothing more. No answer is produced,
 * simulated, or implied.
 */
export function ConversationPane({ disabledReason }: { disabledReason: string | null }) {
  const composer = useRef<ComposerHandle>(null);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        {/* min-h-full centers the first-run state in the empty column while
            still allowing the pane to scroll once it holds a conversation. */}
        <div className="mx-auto flex min-h-full w-full max-w-2xl flex-col justify-center px-6 py-12">
          <h2 className="text-ink text-2xl font-semibold tracking-tight">
            Ask anything about this codebase.
          </h2>
          <p className="text-ink-muted mt-2.5 text-sm leading-relaxed">
            DevPilot retrieves relevant code, explains how it fits together, and keeps answers
            grounded in the repository.
          </p>

          <ul className="divide-line border-line mt-8 divide-y border-y">
            {EXAMPLE_PROMPTS.map((prompt) => (
              <li key={prompt}>
                <button
                  type="button"
                  onClick={() => composer.current?.setValue(prompt)}
                  className="group hover:bg-surface-hover -mx-2 flex w-[calc(100%+1rem)] items-center gap-3 rounded px-2 py-2.5 text-left transition-colors"
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
      </div>

      <Composer handleRef={composer} disabledReason={disabledReason} />
    </div>
  );
}
