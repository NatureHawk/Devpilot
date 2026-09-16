import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * An empty region that teaches instead of apologising. Every empty state
 * answers three questions:
 *
 * - title: what is missing.
 * - description: why it matters — what would be here, and what produces it.
 * - children: the one thing to click to produce it.
 *
 * The dashed outline reads as "a place something will go", which keeps the
 * state distinct from real content without adding another solid card.
 */
export function EmptyState({
  title,
  description,
  children,
  align = "start",
  className,
}: {
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  align?: "center" | "start";
  className?: string;
}) {
  return (
    <div
      className={cn(
        "border-line-strong/70 flex flex-col rounded-lg border border-dashed px-6 py-7",
        align === "center" ? "items-center text-center" : "items-start text-left",
        className,
      )}
    >
      <h2 className="text-ink text-base font-semibold">{title}</h2>
      {description ? (
        <p className="text-ink-muted mt-1.5 max-w-lg text-sm leading-relaxed">{description}</p>
      ) : null}
      {children ? (
        <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2">{children}</div>
      ) : null}
    </div>
  );
}
