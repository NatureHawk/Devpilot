import Link from "next/link";
import type { ReactNode } from "react";

import { SidebarNav } from "@/components/layout/sidebar-nav";
import { Wordmark } from "@/components/layout/wordmark";
import { getCurrentUser, type CurrentUser } from "@/lib/api";

/**
 * Sidebar + main column.
 *
 * Inside a repository the sidebar also carries that repository's workflow
 * (passed in by the repository layout), so "where am I and what comes next"
 * is visible from every screen. The rail collapses to icons below `lg` but
 * never disappears.
 */
export async function AppShell({
  children,
  workflowNav,
}: {
  children: ReactNode;
  workflowNav?: ReactNode;
}) {
  const result = await getCurrentUser();
  const user = result.ok ? result.data : null;

  return (
    <div className="bg-canvas flex h-dvh overflow-hidden">
      <a
        href="#main"
        className="bg-accent text-accent-ink sr-only z-50 rounded-md px-3 py-2 text-sm font-medium focus:not-sr-only focus:absolute focus:top-2 focus:left-2"
      >
        Skip to content
      </a>

      <aside className="border-line bg-surface flex w-14 shrink-0 flex-col border-r lg:w-60">
        <div className="flex h-12 shrink-0 items-center justify-center lg:justify-start lg:px-4">
          <Link href="/" aria-label="DevPilot home" className="rounded-md">
            <Wordmark />
          </Link>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto py-2">
          <SidebarNav set="primary" label="Primary" />
          {workflowNav}
        </div>

        <div className="border-line space-y-1 border-t py-2">
          <SidebarNav set="footer" label="Account" />
          <AccountSummary user={user} />
        </div>
      </aside>

      <div id="main" className="flex min-w-0 flex-1 flex-col">
        {children}
      </div>
    </div>
  );
}

/** The signed-in GitHub account, or an honest signed-out state. */
function AccountSummary({ user }: { user: CurrentUser | null }) {
  const label = user?.github_login ?? "Not signed in";
  const detail = user ? (user.display_name ?? "GitHub account") : "Connect GitHub to begin";
  const initials = (user?.github_login ?? "?").slice(0, 2).toUpperCase();

  return (
    <div className="px-2">
      <div
        title={label}
        className="flex items-center justify-center gap-2.5 rounded-md py-1.5 lg:justify-start lg:px-2"
      >
        <span
          aria-hidden="true"
          className="border-line-strong text-2xs text-ink-muted flex size-6 shrink-0 items-center justify-center rounded-full border font-medium"
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
