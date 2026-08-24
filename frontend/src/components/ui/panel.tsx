import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/** A bordered content region. Depth comes from the border, not from shadow. */
export function Panel({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <section className={cn("border-line bg-surface rounded-lg border", className)}>
      {children}
    </section>
  );
}

export function PanelHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <header className="border-line flex items-start justify-between gap-4 border-b px-4 py-3">
      <div className="min-w-0">
        <h2 className="text-ink text-sm font-medium">{title}</h2>
        {description ? <p className="text-ink-muted mt-0.5 text-xs">{description}</p> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </header>
  );
}
