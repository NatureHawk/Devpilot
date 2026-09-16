import type { ReactNode } from "react";

import { cn } from "@/lib/cn";
import type { JourneyStepId } from "@/lib/workflow";

const POSITION: Record<JourneyStepId, number> = {
  connect: 1,
  index: 2,
  understand: 3,
  investigate: 4,
  review: 5,
  ship: 6,
};

const NAME: Record<JourneyStepId, string> = {
  connect: "Connect",
  index: "Index",
  understand: "Understand",
  investigate: "Investigate",
  review: "Review",
  ship: "Ship",
};

/**
 * "Where am I?" at the top of every stage screen: the step's place in the
 * journey, then what this screen is for.
 */
export function StageHeading({
  step,
  title,
  description,
  className,
}: {
  step: JourneyStepId;
  title: ReactNode;
  description?: ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      <p className="text-2xs text-ink-faint font-semibold tracking-[0.08em] uppercase">
        <span className="text-accent">{NAME[step]}</span>
        <span aria-hidden="true"> · </span>
        <span className="sr-only">, </span>
        Step {POSITION[step]} of 6
      </p>
      <h1 className={cn("text-ink mt-1.5 text-2xl font-semibold tracking-tight")}>{title}</h1>
      {description ? (
        <p className="text-ink-muted mt-2 max-w-2xl text-sm leading-relaxed">{description}</p>
      ) : null}
    </div>
  );
}
