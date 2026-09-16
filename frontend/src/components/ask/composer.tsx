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

export type ComposerHandle = { setValue: (value: string) => void; focus: () => void };

const MAX_ROWS_HEIGHT = 200;

/** The platform is an external fact the server cannot see, and it never changes. */
const noSubscription = () => () => {};
const clientModifier = () => (/Mac|iPhone|iPad/.test(navigator.userAgent) ? "⌘" : "Ctrl");
const serverModifier = () => "Ctrl";

/**
 * The question input. When asking is unavailable it stays typeable but cannot
 * send, and says why.
 *
 * - inline: part of the page, below the guided starting points, before any
 *   question has been asked. It is an option, not the whole screen.
 * - docked: pinned under a conversation for follow-ups.
 */
export function Composer({
  disabledReason,
  handleRef,
  onSubmit,
  busy = false,
  placeholder = "Ask about this repository…",
  variant = "docked",
}: {
  disabledReason: string | null;
  handleRef?: Ref<ComposerHandle>;
  onSubmit?: (question: string) => void;
  busy?: boolean;
  placeholder?: string;
  variant?: "docked" | "inline";
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [value, setValue] = useState("");
  const modifier = useSyncExternalStore(noSubscription, clientModifier, serverModifier);

  useImperativeHandle(handleRef, () => ({
    setValue: (next: string) => {
      setValue(next);
      textareaRef.current?.focus();
    },
    focus: () => textareaRef.current?.focus(),
  }));

  // Grow with the content up to a cap, then scroll inside the field.
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, MAX_ROWS_HEIGHT)}px`;
  }, [value]);

  const canSend = value.trim().length > 0 && disabledReason === null && !busy && !!onSubmit;

  const submit = () => {
    if (!canSend || !onSubmit) return;
    onSubmit(value.trim());
    setValue("");
  };

  return (
    <div className={cn(variant === "docked" && "border-line bg-surface border-t py-3")}>
      <div className={cn(variant === "docked" && "mx-auto w-full max-w-3xl px-6")}>
        <div
          className={cn(
            "border-line-strong bg-canvas rounded-lg border transition-colors",
            "focus-within:border-accent focus-within:ring-accent/20 focus-within:ring-2",
          )}
        >
          <label htmlFor="devpilot-composer" className="sr-only">
            Ask a question about this repository
          </label>
          <textarea
            id="devpilot-composer"
            ref={textareaRef}
            rows={variant === "docked" ? 1 : 2}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              // Ctrl/Cmd+Enter asks; a bare Enter inserts a newline, because
              // questions about code are often multi-line.
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                submit();
              }
            }}
            placeholder={placeholder}
            spellCheck={false}
            aria-describedby={disabledReason ? "composer-availability" : undefined}
            className="text-ink placeholder:text-ink-faint block w-full resize-none bg-transparent px-3 py-2.5 text-sm focus:outline-none"
          />
          <div className="flex items-center gap-2 px-3 pb-2.5">
            <span className="text-2xs text-ink-faint hidden items-center gap-1 sm:flex">
              <Kbd suppressHydrationWarning>{modifier}</Kbd>
              <Kbd>↵</Kbd>
              <span className="ml-0.5">to ask</span>
            </span>
            <button
              type="button"
              onClick={submit}
              disabled={!canSend}
              aria-describedby={disabledReason ? "composer-availability" : undefined}
              className={cn(
                "ml-auto inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium transition-colors active:translate-y-px",
                canSend
                  ? "bg-accent text-accent-ink hover:bg-accent-hover"
                  : "bg-surface-hover text-ink-faint cursor-not-allowed",
              )}
            >
              <ArrowUp aria-hidden="true" className="size-3.5" strokeWidth={2.25} />
              {busy ? "Answering…" : "Ask"}
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
