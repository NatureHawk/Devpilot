import { GitBranch, Lock } from "lucide-react";
import Link from "next/link";

import { ButtonLink } from "@/components/ui/button";
import { JourneySummary, JourneyTrack } from "@/components/ui/journey";
import type { Repository } from "@/lib/api";
import { cn } from "@/lib/cn";
import { repositoryPath } from "@/lib/navigation";
import { deriveJourney, type Workflow } from "@/lib/workflow";

export type RepositoryEntry = { repository: Repository; workflow: Workflow };

/**
 * "Here is where you left off. Here's what you do next."
 *
 * The most recently active repository leads, with its whole journey and the
 * page's one primary action. The rest are quiet rows: how far each has come,
 * and its next step as a secondary action.
 */
export function RepositoryList({ entries }: { entries: RepositoryEntry[] }) {
  const [first, ...rest] = entries;
  if (!first) return null;

  return (
    <div className="space-y-10">
      <section aria-labelledby="continue-heading">
        <h2
          id="continue-heading"
          className="text-ink-faint text-xs font-semibold tracking-[0.08em] uppercase"
        >
          Continue where you left off
        </h2>
        <ContinueRepository entry={first} className="mt-3" />
      </section>

      {rest.length > 0 ? (
        <section aria-labelledby="other-repositories">
          <h2
            id="other-repositories"
            className="text-ink-faint text-xs font-semibold tracking-[0.08em] uppercase"
          >
            Other repositories
          </h2>
          <ul className="border-line bg-surface divide-line mt-3 divide-y rounded-lg border">
            {rest.map((entry) => (
              <RepositoryRow key={entry.repository.id} entry={entry} />
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function ContinueRepository({ entry, className }: { entry: RepositoryEntry; className?: string }) {
  const { repository, workflow } = entry;
  const { owner, name } = repository;
  const steps = deriveJourney(workflow, repository.indexing_status);
  const next = workflow.next;

  return (
    <article
      aria-labelledby={`repository-${repository.id}`}
      className={cn("border-line bg-surface overflow-hidden rounded-lg border", className)}
    >
      <div className="px-5 pt-4 pb-4">
        <RepositoryName repository={repository} size="lg" />
        <JourneyTrack
          className="mt-3"
          steps={steps}
          hrefFor={(step) => repositoryPath(owner, name, step.segment)}
        />
      </div>

      <div className="border-line bg-canvas/40 flex flex-col gap-4 border-t px-5 py-4 sm:flex-row sm:items-center">
        <div className="min-w-0 flex-1">
          <p className="text-2xs text-accent font-semibold tracking-[0.08em] uppercase">
            Next step
          </p>
          <p className="text-ink mt-0.5 text-sm font-semibold">{next.title}</p>
          <p className="text-ink-muted mt-0.5 text-sm leading-relaxed">{next.description}</p>
        </div>
        <ButtonLink
          href={repositoryPath(owner, name, next.segment)}
          variant="primary"
          size="lg"
          forward
          className="shrink-0 self-start sm:self-center"
        >
          {next.label}
        </ButtonLink>
      </div>
    </article>
  );
}

function RepositoryRow({ entry }: { entry: RepositoryEntry }) {
  const { repository, workflow } = entry;
  const steps = deriveJourney(workflow, repository.indexing_status);
  const next = workflow.next;

  return (
    <li className="flex flex-col gap-3 px-4 py-3.5 sm:flex-row sm:items-center sm:gap-4">
      <div className="min-w-0 flex-1">
        <RepositoryName repository={repository} size="md" />
        <JourneySummary className="mt-1.5" steps={steps} status={next.status} />
      </div>
      <ButtonLink
        href={repositoryPath(repository.owner, repository.name, next.segment)}
        variant="secondary"
        size="sm"
        forward
        aria-label={`${next.label} — ${repository.name}`}
        className="shrink-0 self-start sm:self-center"
      >
        {next.label}
      </ButtonLink>
    </li>
  );
}

function RepositoryName({ repository, size }: { repository: Repository; size: "md" | "lg" }) {
  return (
    <div className="flex min-w-0 flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
      <h3
        id={`repository-${repository.id}`}
        className={size === "lg" ? "text-lg font-semibold tracking-tight" : "text-sm font-semibold"}
      >
        <Link
          href={repositoryPath(repository.owner, repository.name)}
          className="text-ink hover:text-accent rounded-sm transition-colors"
        >
          {repository.name}
        </Link>
      </h3>
      <span className="text-ink-faint flex min-w-0 items-center gap-2 text-xs">
        <span className="truncate">{repository.owner}</span>
        {repository.visibility === "private" ? (
          <Lock aria-label="Private" className="size-3 shrink-0" strokeWidth={2} />
        ) : null}
        <span aria-hidden="true">·</span>
        <span className="flex items-center gap-1">
          <GitBranch aria-hidden="true" className="size-3" strokeWidth={1.75} />
          <span className="font-mono">{repository.default_branch}</span>
        </span>
      </span>
    </div>
  );
}
