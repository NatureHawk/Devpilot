"use client";

import {
  FolderGit2,
  GitPullRequest,
  ListChecks,
  type LucideIcon,
  MessageSquareText,
  ScanSearch,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { JourneyMarker } from "@/components/ui/journey";
import { cn } from "@/lib/cn";
import { repositoryPath } from "@/lib/navigation";
import type { Stage, StageId, StageState } from "@/lib/workflow";

const ICONS: Record<StageId, LucideIcon> = {
  repository: FolderGit2,
  ask: MessageSquareText,
  investigate: ScanSearch,
  review: ListChecks,
  ship: GitPullRequest,
};

const GROUPS: { label: string; ids: StageId[] }[] = [
  { label: "Explore", ids: ["repository", "ask"] },
  { label: "Build", ids: ["investigate", "review", "ship"] },
];

const STATE_TEXT: Record<StageState, string> = {
  done: "done",
  current: "next step",
  available: "available",
  locked: "not available yet",
};

/**
 * The repository's workflow as a progression.
 *
 * Steps are joined by a rail so the sidebar reads as a path rather than a list
 * of routes: completed steps are checked, the next step is marked, and steps
 * that still need something are subdued. Every step stays a link — nothing is
 * blocked. The detail line comes from real state (see lib/workflow).
 */
export function WorkflowNav({
  owner,
  repo,
  stages,
}: {
  owner: string;
  repo: string;
  stages: Stage[];
}) {
  const pathname = usePathname();
  const base = repositoryPath(owner, repo);

  return (
    <nav aria-label={`${repo} workflow`} className="px-2">
      <div className="border-line mx-1 mb-1 border-t pt-3 lg:mx-2">
        <p
          className="text-ink hidden truncate text-xs font-semibold lg:block"
          title={`${owner}/${repo}`}
        >
          {repo}
        </p>
      </div>

      {GROUPS.map((group) => {
        const items = group.ids
          .map((id) => stages.find((candidate) => candidate.id === id))
          .filter((stage): stage is Stage => stage !== undefined);

        return (
          <div key={group.label} className="mt-3">
            <p className="text-2xs text-ink-faint hidden px-2 pb-1 font-semibold tracking-[0.08em] uppercase lg:block">
              {group.label}
            </p>
            <ul className="flex flex-col gap-0.5">
              {items.map((stage, index) => {
                const href = repositoryPath(owner, repo, stage.segment);
                const active =
                  stage.segment === ""
                    ? pathname === base
                    : pathname === href || pathname.startsWith(`${href}/`);
                const following = items[index + 1];
                return (
                  <li key={stage.id} className="relative">
                    {following ? (
                      // The rail to the next step: solid once this step is done.
                      <span
                        aria-hidden="true"
                        className={cn(
                          "absolute top-[1.5rem] bottom-[-0.6875rem] left-[0.875rem] z-[1] hidden w-px lg:block",
                          stage.state === "done" ? "bg-success/40" : "bg-line-strong",
                        )}
                      />
                    ) : null}
                    <StageLink stage={stage} href={href} active={active} />
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </nav>
  );
}

function StageLink({ stage, href, active }: { stage: Stage; href: string; active: boolean }) {
  const Icon = ICONS[stage.id];
  const current = stage.state === "current";
  const locked = stage.state === "locked";

  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      title={`${stage.label} — ${stage.detail}`}
      className={cn(
        "relative flex items-center gap-2.5 rounded-md py-1.5 transition-colors",
        "justify-center lg:items-start lg:justify-start lg:px-2",
        active ? "bg-surface-hover" : "hover:bg-surface-hover",
      )}
    >
      {/* Collapsed rail: the step's icon, with a dot on the next step. */}
      <span className="relative lg:hidden" aria-hidden="true">
        <Icon
          className={cn(
            "size-4",
            current ? "text-accent" : locked ? "text-ink-faint/70" : "text-ink-muted",
          )}
          strokeWidth={1.75}
        />
        {current ? (
          <span className="bg-accent absolute -top-0.5 -right-0.5 size-1.5 rounded-full" />
        ) : null}
      </span>

      <span className="mt-[0.1875rem] hidden lg:flex" aria-hidden="true">
        <JourneyMarker
          state={stage.state === "done" ? "done" : current ? "current" : "upcoming"}
          className={locked ? "opacity-60" : undefined}
        />
      </span>

      <span className="sr-only lg:not-sr-only lg:min-w-0 lg:flex-1">
        <span
          className={cn(
            "block truncate text-sm",
            current || active
              ? "text-ink font-medium"
              : locked
                ? "text-ink-faint"
                : "text-ink-muted",
          )}
        >
          {stage.label}
        </span>
        <span
          className={cn(
            "text-2xs block truncate",
            current ? "text-accent" : locked ? "text-ink-faint/80" : "text-ink-faint",
          )}
        >
          {stage.detail}
        </span>
      </span>
      <span className="sr-only">({STATE_TEXT[stage.state]})</span>
    </Link>
  );
}
