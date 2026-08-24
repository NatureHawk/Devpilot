import { GitBranch, Lock, Unlock } from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import type { Repository } from "@/lib/api";

/**
 * Identity bar for the repository workspace.
 *
 * Owner and name come from the URL, so they are always shown. Branch,
 * visibility and the GitHub link come from the stored record and are omitted
 * entirely when the repository is not connected — an unknown value is left out
 * rather than guessed.
 */
export function RepositoryHeader({
  owner,
  repo,
  repository,
}: {
  owner: string;
  repo: string;
  repository: Repository | null;
}) {
  const VisibilityIcon = repository?.visibility === "public" ? Unlock : Lock;

  return (
    <div className="border-line bg-surface flex h-12 shrink-0 items-center gap-3 border-b px-4">
      <div className="flex min-w-0 items-center gap-2">
        {repository ? (
          <VisibilityIcon
            aria-hidden="true"
            className="text-ink-faint size-3.5 shrink-0"
            strokeWidth={1.75}
          />
        ) : null}
        <h1 className="min-w-0 truncate text-sm">
          <Link href="/repositories" className="text-ink-muted hover:text-ink">
            {owner}
          </Link>
          <span aria-hidden="true" className="text-ink-faint px-1">
            /
          </span>
          <span className="text-ink font-medium">{repo}</span>
        </h1>
      </div>

      <div className="ml-auto flex shrink-0 items-center gap-2">
        {repository ? (
          <>
            <span className="text-ink-muted hidden items-center gap-1.5 text-xs sm:flex">
              <GitBranch aria-hidden="true" className="size-3.5" strokeWidth={1.75} />
              <span className="font-mono">{repository.default_branch}</span>
            </span>
            {repository.provider === "github" ? (
              <a
                href={`https://github.com/${repository.owner}/${repository.name}`}
                target="_blank"
                rel="noreferrer noopener"
                className="text-ink-muted hover:text-ink text-xs"
              >
                Open on GitHub
              </a>
            ) : null}
          </>
        ) : (
          <Badge tone="neutral">Not connected</Badge>
        )}
      </div>
    </div>
  );
}
