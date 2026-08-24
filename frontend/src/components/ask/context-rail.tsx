import { GitBranch } from "lucide-react";

import type { Repository } from "@/lib/api";

/**
 * Left rail of the Ask workspace: what DevPilot is looking at, and the threads
 * already open against it.
 */
export function ContextRail({ repository }: { repository: Repository | null }) {
  return (
    <aside
      aria-label="Repository context navigation"
      className="border-line bg-surface hidden w-[248px] shrink-0 flex-col overflow-y-auto border-r xl:flex"
    >
      <header className="border-line flex h-10 shrink-0 items-center border-b px-4">
        <h2 className="text-ink text-xs font-medium">Scope</h2>
      </header>

      <div className="px-4 py-4">
        {repository ? (
          <div className="text-ink-muted flex items-center gap-2 text-xs">
            <GitBranch aria-hidden="true" className="size-3.5 shrink-0" strokeWidth={1.75} />
            <span className="truncate font-mono">{repository.default_branch}</span>
          </div>
        ) : (
          <p className="text-ink-muted text-xs leading-relaxed">
            No branch is in scope until the repository is connected.
          </p>
        )}
      </div>

      <div className="border-line border-t px-4 py-4">
        <h3 className="text-2xs text-ink-faint font-medium tracking-wide uppercase">
          Conversations
        </h3>
        <p className="text-ink-muted mt-2 text-xs leading-relaxed">
          Threads you start about this repository are kept here.
        </p>
      </div>
    </aside>
  );
}
