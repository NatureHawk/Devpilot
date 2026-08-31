import { ExternalLink } from "lucide-react";
import type { Metadata } from "next";

import { NotConnectedState } from "@/components/repository/repository-states";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { ApiErrorState } from "@/components/ui/error-state";
import { Panel } from "@/components/ui/panel";
import { listChanges, type ProposedChange } from "@/lib/api";
import { decodeParams, loadRepository, type RepositoryParams } from "@/lib/repository-context";

export const metadata: Metadata = { title: "Pull requests" };

/** A pull request DevPilot opened, or is one failed step away from opening. */
function isPullRequestRelated(change: ProposedChange): boolean {
  return change.status === "pr_created" || change.status === "committed";
}

export default async function PullRequestsPage({ params }: { params: Promise<RepositoryParams> }) {
  const { owner, repo } = decodeParams(await params);
  const result = await loadRepository(owner, repo);

  if (!result.ok) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-5xl px-6 py-8">
          {result.error.code === "not_found" ? (
            <NotConnectedState owner={owner} repo={repo} />
          ) : (
            <ApiErrorState error={result.error} />
          )}
        </div>
      </div>
    );
  }

  const changesResult = await listChanges(result.data.id);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-6 py-8">
        {!changesResult.ok ? (
          <ApiErrorState error={changesResult.error} />
        ) : (
          <PullRequestsList changes={changesResult.data.items.filter(isPullRequestRelated)} />
        )}
      </div>
    </div>
  );
}

function PullRequestsList({ changes }: { changes: ProposedChange[] }) {
  if (changes.length === 0) {
    return (
      <Panel>
        <EmptyState
          title="No pull requests from DevPilot"
          description="Approve a proposed change and create its pull request from the Changes tab — it will appear here once GitHub confirms it, with a real link to the PR."
        />
      </Panel>
    );
  }

  return (
    <div className="space-y-4">
      {changes.map((change) => (
        <PullRequestCard key={change.id} change={change} />
      ))}
    </div>
  );
}

function PullRequestCard({ change }: { change: ProposedChange }) {
  const open = change.status === "pr_created";

  return (
    <Panel>
      <div className="flex items-start justify-between gap-4 px-4 py-3.5">
        <div className="min-w-0">
          <h3 className="text-ink text-sm font-medium">
            {change.summary || change.request}
          </h3>
          <p className="text-2xs text-ink-faint mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono">
            {open && change.pr_number ? <span>#{change.pr_number}</span> : null}
            {change.branch_name ? <span>{change.branch_name}</span> : null}
            {change.commit_sha ? <span>{change.commit_sha.slice(0, 7)}</span> : null}
            {change.executed_at ? (
              <span className="font-sans">
                {new Date(change.executed_at).toLocaleString()}
              </span>
            ) : null}
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <Badge tone={open ? "success" : "warning"}>
            {open ? "Open on GitHub" : "Not yet opened"}
          </Badge>
          {open && change.pr_url ? (
            <a
              href={change.pr_url}
              target="_blank"
              rel="noreferrer"
              className="text-accent inline-flex items-center gap-1 text-xs font-medium hover:underline"
            >
              View
              <ExternalLink aria-hidden="true" className="size-3" strokeWidth={2} />
            </a>
          ) : null}
        </div>
      </div>

      {!open && change.execution_error ? (
        <div className="border-line border-t px-4 py-2.5">
          <p className="text-ink-muted text-xs">
            {change.execution_error} — retry from the Changes tab.
          </p>
        </div>
      ) : null}
    </Panel>
  );
}
