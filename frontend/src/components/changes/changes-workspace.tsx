"use client";

import { Check, ExternalLink, GitPullRequest, Loader2, Wrench, X } from "lucide-react";
import { useState, useTransition } from "react";

import { DiffView } from "@/components/changes/diff-view";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Panel, PanelHeader } from "@/components/ui/panel";
import { executeChangeAction, proposeChangeAction, reviewChangeAction } from "@/app/actions";
import type { ChangeStatus, ProposedChange } from "@/lib/api";

type ActionError = { code: string; message: string };

const STATUS_TONE: Record<ChangeStatus, "neutral" | "accent" | "success" | "warning" | "danger"> = {
  proposed: "accent",
  approved: "success",
  rejected: "neutral",
  stale: "warning",
  failed: "danger",
  executing: "accent",
  committed: "accent",
  pr_created: "success",
};

const STATUS_LABEL: Record<ChangeStatus, string> = {
  proposed: "Awaiting review",
  approved: "Approved",
  rejected: "Rejected",
  stale: "Stale",
  failed: "Failed",
  executing: "Creating pull request…",
  committed: "Committed",
  pr_created: "Pull request open",
};

/**
 * The change-review workspace: request, investigation, proposal, diff, decision,
 * and — once approved — the real GitHub write path.
 *
 * Staged deliberately, all the way through: a diff arrives with the evidence
 * behind it, approval is a separate explicit act, and creating the pull
 * request is a third, separate explicit act. Nothing reaches GitHub because a
 * proposal merely exists — only because a human clicked each step.
 */
export function ChangesWorkspace({
  repositoryId,
  owner,
  name,
  initialChanges,
  disabledReason,
}: {
  repositoryId: string | null;
  owner: string;
  name: string;
  initialChanges: ProposedChange[];
  disabledReason: string | null;
}) {
  const [changes, setChanges] = useState(initialChanges);
  const [request, setRequest] = useState("");
  const [error, setError] = useState<ActionError | null>(null);
  const [pending, startTransition] = useTransition();
  const [executingId, setExecutingId] = useState<string | null>(null);

  const submit = () => {
    if (!repositoryId || !request.trim() || pending) return;

    startTransition(async () => {
      const outcome = await proposeChangeAction(repositoryId, owner, name, request.trim());
      if (outcome.ok) {
        setChanges((current) => [outcome.data, ...current]);
        setRequest("");
        setError(null);
      } else {
        setError(outcome.error);
      }
    });
  };

  const review = (changeId: string, decision: "approve" | "reject") => {
    startTransition(async () => {
      const outcome = await reviewChangeAction(changeId, decision, owner, name);
      if (outcome.ok) {
        setChanges((current) =>
          current.map((change) => (change.id === outcome.data.id ? outcome.data : change)),
        );
        setError(null);
      } else {
        setError(outcome.error);
      }
    });
  };

  const execute = (changeId: string) => {
    setExecutingId(changeId);
    startTransition(async () => {
      const outcome = await executeChangeAction(changeId, owner, name);
      setExecutingId(null);
      if (outcome.ok) {
        setChanges((current) =>
          current.map((change) => (change.id === outcome.data.id ? outcome.data : change)),
        );
        setError(null);
      } else {
        setError(outcome.error);
      }
    });
  };

  return (
    <div className="space-y-6">
      <Panel>
        <PanelHeader
          title="Request a change"
          description="DevPilot investigates the repository and proposes a patch. Nothing is applied without your approval."
        />
        <div className="p-4">
          <label htmlFor="change-request" className="sr-only">
            Change request
          </label>
          <textarea
            id="change-request"
            rows={3}
            value={request}
            onChange={(event) => setRequest(event.target.value)}
            disabled={disabledReason !== null}
            placeholder="Add input validation to the registration endpoint."
            spellCheck={false}
            className="border-line-strong bg-canvas text-ink placeholder:text-ink-faint focus:border-accent w-full resize-none rounded-md border px-3 py-2.5 text-sm transition-colors focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
          />

          <div className="mt-3 flex items-center gap-3">
            <Button
              variant="primary"
              onClick={submit}
              disabled={pending || disabledReason !== null || request.trim().length === 0}
            >
              {pending ? (
                <Loader2 aria-hidden="true" className="size-3.5 animate-spin" strokeWidth={2} />
              ) : (
                <Wrench aria-hidden="true" className="size-3.5" strokeWidth={2} />
              )}
              {pending ? "Investigating…" : "Propose change"}
            </Button>
            {disabledReason ? (
              <span className="text-2xs text-ink-faint">{disabledReason}</span>
            ) : (
              <span className="text-2xs text-ink-faint">
                Investigation is synchronous and may take a minute.
              </span>
            )}
          </div>

          {error ? (
            <div role="alert" className="border-line mt-3 rounded-md border px-3 py-2.5">
              <p className="text-ink text-sm font-medium">Something went wrong</p>
              <p className="text-ink-muted mt-1 text-sm">{error.message}</p>
              <p className="text-2xs text-ink-faint mt-1.5 font-mono">{error.code}</p>
            </div>
          ) : null}
        </div>
      </Panel>

      {changes.length === 0 ? (
        <Panel>
          <EmptyState
            title="No proposed changes"
            description="AI-generated modifications will appear here for review before anything is written back to the repository."
          />
        </Panel>
      ) : (
        changes.map((change) => (
          <ChangeCard
            key={change.id}
            change={change}
            onReview={review}
            onExecute={execute}
            busy={pending}
            executing={executingId === change.id}
          />
        ))
      )}
    </div>
  );
}

const EXECUTION_STAGES: { event: string; label: string }[] = [
  { event: "execution_started", label: "Started" },
  { event: "branch_created", label: "Branch created" },
  { event: "patch_applied", label: "Patch applied" },
  { event: "commit_created", label: "Committed" },
  { event: "push_completed", label: "Pushed" },
  { event: "pr_created", label: "Pull request opened" },
];

function ChangeCard({
  change,
  onReview,
  onExecute,
  busy,
  executing,
}: {
  change: ProposedChange;
  onReview: (changeId: string, decision: "approve" | "reject") => void;
  onExecute: (changeId: string) => void;
  busy: boolean;
  executing: boolean;
}) {
  const reviewable = change.status === "proposed";
  const canCreatePr = change.status === "approved" || change.status === "committed";
  const reachedEvents = new Set(change.execution_events.map((e) => e.event));

  return (
    <Panel>
      <div className="border-line flex items-start justify-between gap-4 border-b px-4 py-3">
        <div className="min-w-0">
          <h3 className="text-ink text-sm font-medium">{change.request}</h3>
          <p className="text-2xs text-ink-faint mt-1">
            {change.files_changed} file{change.files_changed === 1 ? "" : "s"} ·{" "}
            {change.tool_calls_used} tool call{change.tool_calls_used === 1 ? "" : "s"}
            {change.model ? ` · ${change.model}` : null}
            {change.indexed_commit_sha
              ? ` · ${change.indexed_commit_sha.slice(0, 7)}`
              : null}
          </p>
        </div>
        <Badge tone={STATUS_TONE[change.status]}>{STATUS_LABEL[change.status]}</Badge>
      </div>

      {change.summary ? (
        <div className="border-line border-b px-4 py-3">
          <p className="text-ink-muted text-sm leading-relaxed">{change.summary}</p>
        </div>
      ) : null}

      {change.investigation.length > 0 ? (
        <div className="border-line border-b px-4 py-3">
          <h4 className="text-2xs text-ink-faint font-medium tracking-wide uppercase">
            Investigation
          </h4>
          <ul className="mt-2 space-y-1">
            {change.investigation.map((step, index) => (
              <li key={index} className="text-ink-muted flex items-center gap-2 text-xs">
                <span className="text-ink font-mono">{step.tool}</span>
                {step.detail ? <span className="truncate">{step.detail}</span> : null}
                <span className="text-ink-faint ml-auto shrink-0 font-mono">
                  {step.duration_ms}ms
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {change.status === "stale" ? (
        <div role="alert" className="border-line border-b px-4 py-3">
          <p className="text-ink text-sm font-medium">This proposal is out of date</p>
          <p className="text-ink-muted mt-1 text-sm">
            The repository was re-indexed after this patch was generated, so it no longer matches
            the current source. Request the change again.
          </p>
        </div>
      ) : null}

      {change.error ? (
        <div className="border-line border-b px-4 py-3">
          <p className="text-ink-muted text-sm">{change.error}</p>
        </div>
      ) : null}

      {change.diff ? <DiffView diff={change.diff} /> : null}

      {/* Execution progress: only shown once approval has actually led somewhere. */}
      {change.status === "executing" ||
      change.status === "committed" ||
      change.status === "pr_created" ||
      (change.status === "failed" && change.execution_events.length > 0) ? (
        <div className="border-line border-b px-4 py-3">
          <h4 className="text-2xs text-ink-faint font-medium tracking-wide uppercase">
            Pull request progress
          </h4>
          <ol className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-xs">
            {EXECUTION_STAGES.map((stage, index) => {
              const done = reachedEvents.has(stage.event);
              return (
                <li key={stage.event} className="flex items-center gap-2">
                  {index > 0 ? <span className="text-ink-faint">→</span> : null}
                  <span className={done ? "text-ink" : "text-ink-faint"}>{stage.label}</span>
                </li>
              );
            })}
          </ol>
          {change.branch_name ? (
            <p className="text-ink-faint mt-2 font-mono text-2xs">{change.branch_name}</p>
          ) : null}
          {change.execution_error ? (
            <p className="text-danger mt-2 text-xs">{change.execution_error}</p>
          ) : null}
        </div>
      ) : null}

      {change.status === "pr_created" && change.pr_url ? (
        <div className="border-line bg-surface flex items-center justify-between gap-3 border-b px-4 py-3">
          <p className="text-ink text-sm">
            Pull request <span className="font-mono">#{change.pr_number}</span> is open on GitHub.
          </p>
          <a
            href={change.pr_url}
            target="_blank"
            rel="noreferrer"
            className="text-accent inline-flex items-center gap-1.5 text-sm font-medium hover:underline"
          >
            View on GitHub
            <ExternalLink aria-hidden="true" className="size-3.5" strokeWidth={2} />
          </a>
        </div>
      ) : null}

      <div className="border-line flex items-center gap-2 border-t px-4 py-3">
        {change.status === "pr_created" ? (
          <span className="text-2xs text-ink-faint">
            This proposal is complete. Merging happens on GitHub, not in DevPilot.
          </span>
        ) : canCreatePr ? (
          <>
            <Button
              variant="primary"
              size="sm"
              onClick={() => onExecute(change.id)}
              disabled={busy}
            >
              {executing ? (
                <Loader2 aria-hidden="true" className="size-3.5 animate-spin" strokeWidth={2} />
              ) : (
                <GitPullRequest aria-hidden="true" className="size-3.5" strokeWidth={2} />
              )}
              {change.status === "committed"
                ? executing
                  ? "Retrying…"
                  : "Retry pull request"
                : executing
                  ? "Creating…"
                  : "Create pull request"}
            </Button>
            <span className="text-2xs text-ink-faint">
              {change.status === "committed"
                ? "The branch and commit already exist; only opening the PR failed."
                : "Creates a branch, commits the exact approved patch, and opens a GitHub PR."}
            </span>
          </>
        ) : (
          <>
            <Button
              variant="primary"
              size="sm"
              onClick={() => onReview(change.id, "approve")}
              disabled={!reviewable || busy}
            >
              <Check aria-hidden="true" className="size-3.5" strokeWidth={2} />
              Approve
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => onReview(change.id, "reject")}
              disabled={!reviewable || busy}
            >
              <X aria-hidden="true" className="size-3.5" strokeWidth={2} />
              Reject
            </Button>
            <span className="text-2xs text-ink-faint ml-auto">
              Approval is recorded in DevPilot. Nothing is committed or pushed yet.
            </span>
          </>
        )}
      </div>
    </Panel>
  );
}
