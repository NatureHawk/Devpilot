import type { ReactNode } from "react";

import { SidebarNav } from "@/components/layout/sidebar-nav";
import { Wordmark } from "@/components/layout/wordmark";
import { getCurrentUser, type CurrentUser } from "@/lib/api";

/**
 * Two-column workspace: a navigation rail and a scrollable main column.
 *
 * The rail keeps its labels on wide screens and collapses to icons below `lg`,
 * where a 232px sidebar would take space the three-pane Ask workspace needs.
 * It never disappears — navigation stays reachable at every width.
 */
export async function AppShell({ children }: { children: ReactNode }) {
  const result = await getCurrentUser();
  const user = result.ok ? result.data : null;
  return (
    <div className="bg-canvas flex h-dvh overflow-hidden">
      <aside className="border-line bg-surface flex w-14 shrink-0 flex-col border-r lg:w-[232px]">
        <div className="flex h-12 items-center justify-center lg:justify-start lg:px-4">
          <Wordmark />
        </div>

        <div className="flex-1 overflow-y-auto py-2">
          <SidebarNav />
        </div>

        <AccountSection user={user} />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">{children}</div>
    </div>
  );
}

/** The signed-in GitHub account, or an honest signed-out state. */
function AccountSection({ user }: { user: CurrentUser | null }) {
  const label = user?.github_login ?? "Not signed in";
  const detail = user ? (user.display_name ?? "GitHub account") : "Sign in to connect a repository";
  const initials = (user?.github_login ?? "?").slice(0, 2).toUpperCase();

  return (
    <div className="border-line border-t p-2">
      <div
        title={label}
        className="flex items-center justify-center gap-2.5 rounded-md py-1.5 lg:justify-start lg:px-2"
      >
        <span
          aria-hidden="true"
          className="border-line-strong text-2xs text-ink-muted flex size-6 shrink-0 items-center justify-center rounded border font-medium"
        >
          {initials}
        </span>
        <span className="hidden min-w-0 lg:block">
          <span className="text-ink block truncate text-xs font-medium">{label}</span>
          <span className="text-2xs text-ink-faint block truncate">{detail}</span>
        </span>
      </div>
    </div>
  );
}
