import { Check } from "lucide-react";
import Link from "next/link";
import { Fragment } from "react";

import { cn } from "@/lib/cn";
import type { JourneyState, JourneyStep } from "@/lib/workflow";

const STATE_TEXT: Record<JourneyState, string> = {
  done: "done",
  current: "you are here",
  upcoming: "not started",
};

/**
 * The repository's journey, left to right: what is done, where you are, and
 * what comes after. Steps are links when `hrefFor` is given — progression is
 * shown, never enforced.
 */
export function JourneyTrack({
  steps,
  hrefFor,
  className,
}: {
  steps: JourneyStep[];
  hrefFor?: (step: JourneyStep) => string;
  className?: string;
}) {
  return (
    <ol
      aria-label="Repository progress"
      className={cn("flex flex-wrap items-center gap-y-2", className)}
    >
      {steps.map((step, index) => {
        const content = (
          <>
            <JourneyMarker state={step.state} attention={step.attention} />
            <span
              className={cn(
                "text-xs whitespace-nowrap",
                step.state === "current"
                  ? step.attention
                    ? "text-danger font-semibold"
                    : "text-ink font-semibold"
                  : step.state === "done"
                    ? "text-ink-muted"
                    : "text-ink-faint",
              )}
            >
              {step.label}
            </span>
            <span className="sr-only">({STATE_TEXT[step.state]})</span>
          </>
        );
        const href = hrefFor?.(step);

        return (
          <Fragment key={step.id}>
            {index > 0 ? (
              <li
                aria-hidden="true"
                className={cn(
                  "mx-2 hidden h-px w-6 sm:block lg:w-10",
                  step.state === "upcoming" ? "bg-line-strong" : "bg-success/45",
                )}
              />
            ) : null}
            <li className={cn(index > 0 && "ml-3 sm:ml-0")}>
              {href ? (
                <Link
                  href={href}
                  aria-current={step.state === "current" ? "step" : undefined}
                  className="hover:bg-surface-hover -mx-1.5 flex items-center gap-1.5 rounded-md px-1.5 py-1 transition-colors"
                >
                  {content}
                </Link>
              ) : (
                <span
                  aria-current={step.state === "current" ? "step" : undefined}
                  className="flex items-center gap-1.5 py-1"
                >
                  {content}
                </span>
              )}
            </li>
          </Fragment>
        );
      })}
    </ol>
  );
}

/**
 * The journey so far in one line — completed steps as accomplishments, then the
 * current status: "✓ Connected ✓ Indexed ● Ready to explore".
 */
export function JourneySummary({
  steps,
  status,
  className,
}: {
  steps: JourneyStep[];
  status: string;
  className?: string;
}) {
  const current = steps.find((step) => step.state === "current");
  const done = steps.filter((step) => step.state === "done");

  return (
    <ul
      aria-label="Progress"
      className={cn("flex flex-wrap items-center gap-x-3.5 gap-y-1 text-xs", className)}
    >
      {done.map((step) => (
        <li key={step.id} className="text-ink-muted flex items-center gap-1.5">
          <JourneyMarker state="done" />
          {step.doneLabel}
        </li>
      ))}
      <li
        className={cn(
          "flex items-center gap-1.5 font-medium",
          current?.attention ? "text-danger" : "text-ink",
        )}
      >
        <JourneyMarker state="current" attention={current?.attention ?? false} />
        {status}
      </li>
    </ul>
  );
}

export function JourneyMarker({
  state,
  attention = false,
  className,
}: {
  state: JourneyState;
  attention?: boolean;
  className?: string | undefined;
}) {
  if (state === "done") {
    return (
      <span
        aria-hidden="true"
        className={cn(
          "bg-success/15 text-success flex size-3.5 shrink-0 items-center justify-center rounded-full",
          className,
        )}
      >
        <Check className="size-2.5" strokeWidth={3} />
      </span>
    );
  }
  if (state === "current") {
    return (
      <span
        aria-hidden="true"
        className={cn(
          "flex size-3.5 shrink-0 items-center justify-center rounded-full border-[1.5px]",
          attention ? "border-danger" : "border-accent",
          className,
        )}
      >
        <span className={cn("size-1.5 rounded-full", attention ? "bg-danger" : "bg-accent")} />
      </span>
    );
  }
  return (
    <span
      aria-hidden="true"
      className={cn("border-line-strong size-3.5 shrink-0 rounded-full border-[1.5px]", className)}
    />
  );
}
