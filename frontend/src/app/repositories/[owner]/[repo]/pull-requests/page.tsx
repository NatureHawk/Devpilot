import { ExternalLink } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { summarizeDiff } from "@/components/changes/diff-files";
import { ShipButton } from "@/components/changes/ship-button";
import { StageHeading } from "@/components/layout/stage-heading";
import { NotConnectedState } from "@/components/repository/repository-states";
import { ButtonLink } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ApiErrorState } from "@/components/ui/error-state";
import type { ProposedChange } from "@/lib/api";
import { repositoryPath } from "@/lib/navigation";
import {
  decodeParams,
  loadChanges,
  loadRepository,
  loadWorkflowContext,
  type RepositoryParams,
} from "@/lib/repository-context";

export const metadata: Metadata = { title: "Ship" };

export default async function ShipPage({ params }: { params: Promise<RepositoryParams> }) {
  const { owner, repo } = decodeParams(await params);
  const result = await loadRepository(owner, repo);

  if (!result.ok) {
    return (
      <Body>
        {result.error.code === "not_found" ? (
          <NotConnectedState owner={owner} repo={repo} />
        ) : (
          <ApiErrorState error={result.error} />
        )}
      </Body>
    );
  }

  const [changesResult, context] = await Promise.all([
    loadChanges(result.data.id),
    loadWorkflowContext(result.data),
  ]);
  const next = context.workflow.next;
  if (!changesResult.ok) {
    return (
      <Body>
        <ApiErrorState error={changesResult.error} />
      </Body>
    );
  }

  const changes = changesResult.data.items;
  const ready = changes.filter((change) =>
    ["approved", "committed", "executing"].includes(change.status),
  );
  const shipped = changes.filter((change) => change.status === "pr_created");
  const awaitingReview = changes.some((change) => change.status === "proposed");

  return (
    <Body>
      <StageHeading
        step="ship"
        title="Ship approved changes"
        description="Approved changes become pull requests: DevPilot creates a branch, commits the exact diff you approved, and opens the PR. Merging stays on GitHub."
      />

      {ready.length === 0 && shipped.length === 0 ? (
        <div className="mt-8">
          {awaitingReview ? (
            <EmptyState
              title="Nothing approved yet"
              description="A proposed change is waiting for your decision. Approve it, and it will be ready to ship here."
            >
              <ButtonLink
                href={repositoryPath(owner, repo, "review")}
                variant="primary"
                size="lg"
                forward
              >
                Review proposed change
              </ButtonLink>
            </EmptyState>
          ) : (
            <EmptyState
              title="Nothing to ship yet"
              description={
                next.stage === "ask" || next.stage === "repository"
                  ? "Shipping starts with understanding. Ask a question, investigate a change from what you learn, and approve its diff — it will be ready to ship here."
                  : "Investigate a change and approve its diff. Approved changes appear here, ready to become pull requests."
              }
            >
              <ButtonLink
                href={repositoryPath(owner, repo, next.segment)}
                variant="primary"
                size="lg"
                forward
              >
                {next.label}
              </ButtonLink>
            </EmptyState>
          )}
        </div>
      ) : null}

      {ready.length > 0 ? (
        <section aria-labelledby="ready-to-ship" className="mt-8">
          <h2 id="ready-to-ship" className="text-ink text-base font-semibold">
            Ready to ship <span className="text-ink-faint font-normal">· {ready.length}</span>
          </h2>
          <ul className="mt-3 space-y-3">
            {ready.map((change) => (
              <li key={change.id} className="border-line bg-surface rounded-lg border px-5 py-4">
                <ReadyChange change={change} owner={owner} repo={repo} />
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {shipped.length > 0 ? (
        <section aria-labelledby="shipped" className="mt-10">
          <h2 id="shipped" className="text-ink text-base font-semibold">
            Pull requests opened{" "}
            <span className="text-ink-faint font-normal">· {shipped.length}</span>
          </h2>
          <ul className="border-line divide-line bg-surface mt-3 divide-y rounded-lg border">
            {shipped.map((change) => (
              <li key={change.id} className="flex items-center gap-4 px-5 py-3">
                <div className="min-w-0 flex-1">
                  <p className="text-ink truncate text-sm font-medium">{change.request}</p>
                  <p className="text-ink-faint mt-0.5 flex flex-wrap gap-x-2 text-xs">
                    {change.pr_number ? (
                      <span className="font-mono">#{change.pr_number}</span>
                    ) : null}
                    {change.branch_name ? (
                      <span className="font-mono">{change.branch_name}</span>
                    ) : null}
                    {change.executed_at ? (
                      <time dateTime={change.executed_at} suppressHydrationWarning>
                        {new Date(change.executed_at).toLocaleString()}
                      </time>
                    ) : null}
                  </p>
                </div>
                {change.pr_url ? (
                  <a
                    href={change.pr_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-accent inline-flex shrink-0 items-center gap-1 text-sm font-medium hover:underline"
                  >
                    View on GitHub
                    <ExternalLink aria-hidden="true" className="size-3.5" strokeWidth={2} />
                    <span className="sr-only">(opens in a new tab)</span>
                  </a>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </Body>
  );
}

function ReadyChange({
  change,
  owner,
  repo,
}: {
  change: ProposedChange;
  owner: string;
  repo: string;
}) {
  const files = summarizeDiff(change.diff);

  return (
    <>
      <p className="text-ink text-sm font-semibold">{change.request}</p>
      {change.summary ? (
        <p className="text-ink-muted mt-1 line-clamp-2 text-sm">{change.summary}</p>
      ) : null}
      <p className="text-ink-faint mt-1 text-xs">
        {files.length} file{files.length === 1 ? "" : "s"} ·{" "}
        <Link
          href={`${repositoryPath(owner, repo, "review")}#change-${change.id}`}
          className="hover:text-ink underline-offset-2 hover:underline"
        >
          view the approved diff
        </Link>
      </p>
      {change.status === "committed" && change.execution_error ? (
        <p className="text-danger mt-2 text-sm">{change.execution_error}</p>
      ) : null}
      <div className="mt-3">
        <ShipButton change={change} owner={owner} name={repo} />
      </div>
    </>
  );
}

function Body({ children }: { children: React.ReactNode }) {
  return (
    <main className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-6 py-8">{children}</div>
    </main>
  );
}
