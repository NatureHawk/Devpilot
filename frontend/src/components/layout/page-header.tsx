import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * The 48px contextual bar at the top of the main column. Fixed height keeps the
 * content area from shifting between routes.
 */
export function PageHeader({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <header
      className={cn(
        "border-line bg-surface flex h-12 shrink-0 items-center gap-3 border-b px-4",
        className,
      )}
    >
      {children}
    </header>
  );
}

export function PageTitle({ children }: { children: ReactNode }) {
  return <h1 className="text-ink truncate text-sm font-medium">{children}</h1>;
}

/** Scroll container for page content, with the standard measure and padding. */
export function PageBody({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <main className={cn("min-h-0 flex-1 overflow-y-auto", className)}>
      <div className="mx-auto w-full max-w-5xl px-6 py-8">{children}</div>
    </main>
  );
}
