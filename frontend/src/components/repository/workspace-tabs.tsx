"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/cn";
import { repositoryPath, WORKSPACE_TABS } from "@/lib/navigation";

export function WorkspaceTabs({ owner, repo }: { owner: string; repo: string }) {
  const pathname = usePathname();
  const base = repositoryPath(owner, repo);

  return (
    <nav
      aria-label="Repository sections"
      className="border-line bg-surface flex h-10 shrink-0 items-end gap-1 border-b px-3"
    >
      {WORKSPACE_TABS.map((tab) => {
        const href = repositoryPath(owner, repo, tab.segment);
        const active = tab.segment === "" ? pathname === base : pathname === href;

        return (
          <Link
            key={tab.label}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              // The active marker sits on the container's border line.
              "relative -mb-px flex h-9 items-center rounded-t-md border-b-2 px-2.5 text-sm transition-colors",
              active
                ? "border-accent text-ink font-medium"
                : "text-ink-muted hover:text-ink border-transparent",
            )}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
