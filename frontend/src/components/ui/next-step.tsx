import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * The answer to "what should I do now?".
 *
 * Appears after every meaningful state change with exactly one dominant action
 * (a `forward` primary button) and at most one quiet alternative. It is a
 * labelled region so screen-reader users can jump straight to it.
 */
export function NextStep({
  title,
  description,
  action,
  secondary,
  eyebrow = "Next step",
  className,
  id,
}: {
  title: string;
  description?: ReactNode;
  /** The primary action — a single `size="lg"` primary button or link. */
  action: ReactNode;
  secondary?: ReactNode;
  eyebrow?: string | undefined;
  className?: string | undefined;
  id?: string | undefined;
}) {
  const headingId = id ?? `next-step-${slugify(title)}`;

  return (
    <section
      aria-labelledby={headingId}
      className={cn(
        "border-accent/25 bg-accent-soft animate-in relative overflow-hidden rounded-lg border py-4 pr-5 pl-6",
        // The rail ties every next step together visually, on every screen.
        "before:bg-accent before:absolute before:inset-y-0 before:left-0 before:w-[3px]",
        className,
      )}
    >
      <p className="text-2xs text-accent font-semibold tracking-[0.08em] uppercase">{eyebrow}</p>
      <h2 id={headingId} className="text-ink mt-1 text-lg font-semibold tracking-tight">
        {title}
      </h2>
      {description ? (
        <p className="text-ink-muted mt-1 max-w-2xl text-sm leading-relaxed">{description}</p>
      ) : null}
      {action || secondary ? (
        <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2">
          {action}
          {secondary}
        </div>
      ) : null}
    </section>
  );
}

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 48);
}
