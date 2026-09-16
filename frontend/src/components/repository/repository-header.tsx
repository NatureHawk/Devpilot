import { ExternalLink, GitBranch, Lock } from "lucide-react";
import Link from "next/link";

import { SectionCrumb } from "@/components/repository/section-crumb";
import { Badge } from "@/components/ui/badge";
import type { Repository } from "@/lib/api";
import { repositoryPath } from "@/lib/navigation";

/**
 * "Where am I": Repositories / RetailHub / Ask.
 *
 * Owner and name come from the URL, so they always show. Branch, visibility and
 * the GitHub link come from the stored record and are left out entirely when
 * the repository is not connected.
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
  return (
    <header className="border-line bg-surface flex h-12 shrink-0 items-center gap-3 border-b px-4">
      <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1.5 text-sm">
        <Link href="/" className="text-ink-muted hover:text-ink hidden shrink-0 sm:inline">
          Repositories
        </Link>
        <span aria-hidden="true" className="text-ink-faint hidden sm:inline">
          /
        </span>
        <Link
          href={repositoryPath(owner, repo)}
          title={`${owner}/${repo}`}
          className="text-ink hover:text-ink truncate font-medium"
        >
          {repo}
        </Link>
        {repository?.visibility === "private" ? (
          <Lock
            aria-label="Private repository"
            className="text-ink-faint size-3 shrink-0"
            strokeWidth={2}
          />
        ) : null}
        <SectionCrumb owner={owner} repo={repo} />
      </nav>

      <div className="ml-auto flex shrink-0 items-center gap-3">
        {repository ? (
          <>
            <span className="text-ink-faint hidden items-center gap-1.5 text-xs md:flex">
              <GitBranch aria-hidden="true" className="size-3.5" strokeWidth={1.75} />
              <span className="font-mono">{repository.default_branch}</span>
            </span>
            {repository.provider === "github" ? (
              <a
                href={`https://github.com/${repository.owner}/${repository.name}`}
                target="_blank"
                rel="noreferrer noopener"
                className="text-ink-muted hover:text-ink inline-flex items-center gap-1 text-xs"
              >
                GitHub
                <ExternalLink aria-hidden="true" className="size-3" strokeWidth={2} />
                <span className="sr-only">(opens in a new tab)</span>
              </a>
            ) : null}
          </>
        ) : (
          <Badge tone="neutral">Not connected</Badge>
        )}
      </div>
    </header>
  );
}
