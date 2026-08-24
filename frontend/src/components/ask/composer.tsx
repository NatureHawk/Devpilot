"use client";

import { ArrowUp } from "lucide-react";
import {
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  useSyncExternalStore,
  type Ref,
} from "react";

import { Kbd } from "@/components/ui/kbd";
import { cn } from "@/lib/cn";

export type ComposerHandle = { setValue: (value: string) => void };

const MAX_ROWS_HEIGHT = 200;

/** The platform is an external fact the server cannot see, and it never changes. */
const noSubscription = () => () => {};
const clientModifier = () => (/Mac|iPhone|iPad/.test(navigator.userAgent) ? "⌘" : "Ctrl");
const serverModifier = () => "Ctrl";

/**
 * The question input.
 *
 * Sending is genuinely unavailable in this milestone, so the control is
 * disabled and says why. Typing, autosizing and the suggestion fill are real —
 * only the network call is missing, and nothing pretends otherwise.
 */
export function Composer({
  disabledReason,
  handleRef,
}: {
  disabledReason: string | null;
  handleRef?: Ref<ComposerHandle>;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [value, setValue] = useState("");
  const modifier = useSyncExternalStore(noSubscription, clientModifier, serverModifier);

  useImperativeHandle(handleRef, () => ({
    setValue: (next: string) => {
      setValue(next);
      textareaRef.current?.focus();
    },
  }));

  // Grow with the content up to a cap, then scroll inside the field.
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, MAX_ROWS_HEIGHT)}px`;
  }, [value]);

  const canSend = value.trim().length > 0 && disabledReason === null;

  return (
    <div className="border-line bg-surface border-t py-3">
      {/* Matches the conversation measure so the input lines up with the thread. */}
      <div className="mx-auto w-full max-w-2xl px-6">
        <div
          className={cn(
            "border-line-strong bg-canvas rounded-lg border transition-colors",
            "focus-within:border-accent",
          )}
        >
          <label htmlFor="devpilot-composer" className="sr-only">
            Ask a question about this repository
          </label>
          <textarea
            id="devpilot-composer"
            ref={textareaRef}
            rows={2}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="Ask about this repository…"
            spellCheck={false}
            className="text-ink placeholder:text-ink-faint block w-full resize-none bg-transparent px-3 py-2.5 text-sm focus:outline-none"
          />
          <div className="flex items-center gap-2 px-3 pb-2.5">
            <span className="text-2xs text-ink-faint flex items-center gap-1">
              <Kbd suppressHydrationWarning>{modifier}</Kbd>
              <Kbd>↵</Kbd>
              <span className="ml-0.5">to send</span>
            </span>
            <button
              type="button"
              disabled={!canSend}
              aria-label="Send question"
              aria-describedby={disabledReason ? "composer-availability" : undefined}
              className={cn(
                "ml-auto inline-flex size-7 items-center justify-center rounded-md transition-colors",
                canSend
                  ? "bg-accent text-accent-ink hover:bg-accent-hover"
                  : "bg-surface-hover text-ink-faint cursor-not-allowed",
              )}
            >
              <ArrowUp aria-hidden="true" className="size-3.5" strokeWidth={2.25} />
            </button>
          </div>
        </div>

        {disabledReason ? (
          <p id="composer-availability" className="text-2xs text-ink-faint mt-2">
            {disabledReason}
          </p>
        ) : null}
      </div>
    </div>
  );
}
