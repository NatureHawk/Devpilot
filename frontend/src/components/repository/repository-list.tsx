import { GitBranch, Lock, Unlock } from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import type { IndexingStatus, Repository } from "@/lib/api";
import { repositoryPath } from "@/lib/navigation";

const INDEXING_LABEL: Record<
  IndexingStatus,
  { label: string; tone: "neutral" | "accent" | "success" | "warning" | "danger" }
> = {
  not_indexed: { label: "Not indexed", tone: "neutral" },
  indexing: { label: "Indexing", tone: "accent" },
  indexed: { label: "Indexed", tone: "success" },
  failed: { label: "Index failed", tone: "danger" },
};

export function RepositoryRow({ repository }: { repository: Repository }) {
  const status = INDEXING_LABEL[repository.indexing_status];
  const VisibilityIcon = repository.visibility === "private" ? Lock : Unlock;

  return (
    <Link
      href={repositoryPath(repository.owner, repository.name)}
      className="hover:bg-surface-hover flex items-center gap-3 px-4 py-3 transition-colors"
    >
      <VisibilityIcon
        aria-hidden="true"
        className="text-ink-faint size-3.5 shrink-0"
        strokeWidth={1.75}
      />
      <span className="min-w-0 truncate text-sm">
        <span className="text-ink-muted">{repository.owner}/</span>
        <span className="text-ink font-medium">{repository.name}</span>
      </span>
      <span className="ml-auto flex shrink-0 items-center gap-3">
        <span className="text-ink-faint hidden items-center gap-1.5 text-xs sm:flex">
          <GitBranch aria-hidden="true" className="size-3.5" strokeWidth={1.75} />
          <span className="font-mono">{repository.default_branch}</span>
        </span>
        <Badge tone={status.tone}>{status.label}</Badge>
      </span>
    </Link>
  );
}

export function RepositoryList({ repositories }: { repositories: Repository[] }) {
  return (
    <ul className="divide-line divide-y">
      {repositories.map((repository) => (
        <li key={repository.id}>
          <RepositoryRow repository={repository} />
        </li>
      ))}
    </ul>
  );
}
