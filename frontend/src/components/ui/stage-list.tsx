import { CircleCheck, Loader2 } from "lucide-react";

import { cn } from "@/lib/cn";

/**
 * - done / active / pending: the application observed this stage's progress
 *   (for example, sources arriving before answer text).
 * - running: part of a single request whose internal progress is not
 *   observable. Shown as happening together — never as ticking off one by one,
 *   which would invent progress that is not known.
 */
export type StageItemState = "done" | "active" | "pending" | "running";

export type StageItem = { label: string; state: StageItemState };

export function StageList({
  items,
  label,
  className,
}: {
  items: StageItem[];
  label: string;
  className?: string;
}) {
  return (
    <ol aria-label={label} className={cn("space-y-1.5", className)}>
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-2.5 text-sm">
          <StageIcon state={item.state} />
          <span
            className={cn(
              item.state === "done" && "text-ink-muted",
              item.state === "active" && "text-ink font-medium",
              item.state === "pending" && "text-ink-faint",
              item.state === "running" && "text-ink-muted",
            )}
          >
            {item.label}
          </span>
          <span className="sr-only">
            {item.state === "done"
              ? "(done)"
              : item.state === "active"
                ? "(in progress)"
                : item.state === "pending"
                  ? "(not started)"
                  : ""}
          </span>
        </li>
      ))}
    </ol>
  );
}

function StageIcon({ state }: { state: StageItemState }) {
  if (state === "done") {
    return (
      <CircleCheck aria-hidden="true" className="text-success size-4 shrink-0" strokeWidth={2} />
    );
  }
  if (state === "active") {
    return (
      <Loader2
        aria-hidden="true"
        className="text-accent size-4 shrink-0 animate-spin"
        strokeWidth={2}
      />
    );
  }
  return (
    <span aria-hidden="true" className="flex size-4 shrink-0 items-center justify-center">
      <span
        className={cn(
          "size-1.5 rounded-full",
          state === "running" ? "bg-accent animate-pulse" : "bg-line-strong",
        )}
      />
    </span>
  );
}
