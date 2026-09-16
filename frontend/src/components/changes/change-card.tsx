"use client";

import {
  Check,
  ChevronRight,
  ExternalLink,
  GitPullRequest,
  Loader2,
  TriangleAlert,
} from "lucide-react";
import { useState } from "react";

import { executeChangeAction, reviewChangeAction } from "@/app/actions";
import { summarizeDiff } from "@/components/changes/diff-files";
import { DiffView } from "@/components/changes/diff-view";
import { CHANGE_STATUS_LABEL } from "@/components/repository/recent-work";
import { Button, ButtonLink, buttonStyles } from "@/components/ui/button";
import { StageList } from "@/components/ui/stage-list";
import type { ChangeStatus, ProposedChange } from "@/lib/api";
import { cn } from "@/lib/cn";
import { repositoryPath } from "@/lib/navigation";

type Pending = "approve" | "reject" | "execute" | null;

const EXECUTION_STAGES: { event: string; label: string }[] = [
  { event: "execution_started", label: "Started" },
  { event: "branch_created", label: "Branch created" },
  { event: "patch_applied", label: "Approved diff applied" },
  { event: "commit_created", label: "Committed" },
  { event: "push_completed", label: "Pushed" },
  { event: "pr_created", label: "Pull request opened" },
];

const STATUS_TONE: Record<ChangeStatus, string> = {
  proposed: "border-accent/40 text-accent",
  approved: "border-success/40 text-success",
  executing: "border-accent/40 text-accent",
  committed: "border-warning/40 text-warning",
  pr_created: "border-success/40 text-success",
  rejected: "border-line-strong text-ink-muted",
  stale: "border-warning/40 text-warning",
  failed: "border-danger/40 text-danger",
};

/**
 * One proposed change, laid out as a review:
 * request → what DevPilot found → files affected → how it investigated → diff → decision.
 *
 * Each status offers exactly one next action. Approval and pull-request creation
 * stay separate, explicit acts: nothing reaches GitHub because a proposal
 * exists, only because a person pressed each button.
 */
export function ChangeCard({
  change,
  owner,
  name,
  onChange,
}: {
  change: ProposedChange;
  owner: string;
  name: string;
  onChange: (change: ProposedChange) => void;
}) {
  const [pending, setPending] = useState<Pending>(null);
  const [confirmReject, setConfirmReject] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const files = summarizeDiff(change.diff);
  const headingId = `change-${change.id}-title`;
  const reached = new Set(change.execution_events.map((event) => event.event));
  const showExecution =
    change.status === "executing" ||
    change.status === "committed" ||
    change.status === "pr_created" ||
    (change.status === "failed" && change.execution_events.length > 0);

  const act = async (kind: Exclude<Pending, null>) => {
    setPending(kind);
    setError(null);
    const outcome =
      kind === "execute"
        ? await executeChangeAction(change.id, owner, name)
        : await reviewChangeAction(change.id, kind, owner, name);
    setPending(null);
    setConfirmReject(false);
    if (outcome.ok) onChange(outcome.data);
    else setError(outcome.error.message);
  };

  const investigateAgainHref = `${repositoryPath(owner, name, "changes")}?request=${encodeURIComponent(change.request)}`;

  return (
    <article
      id={`change-${change.id}`}
      aria-labelledby={headingId}
      className="border-line bg-surface target:border-accent target:ring-accent/30 scroll-mt-6 overflow-hidden rounded-lg border target:ring-1"
    >
      <header className="px-5 pt-4 pb-3">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="text-ink-faint text-xs font-semibold tracking-[0.08em] uppercase">
              Change request
            </p>
            <h3 id={headingId} className="text-ink mt-1 text-base font-semibold">
              {change.request}
            </h3>
            <p className="text-ink-faint mt-1 text-xs">
              <time dateTime={change.created_at} suppressHydrationWarning>
                {new Date(change.created_at).toLocaleString()}
              </time>
              {` · ${change.files_changed} file${change.files_changed === 1 ? "" : "s"}`}
              {` · ${change.tool_calls_used} investigation step${change.tool_calls_used === 1 ? "" : "s"}`}
              {change.indexed_commit_sha ? (
                <>
                  {" · at "}
                  <span className="font-mono">{change.indexed_commit_sha.slice(0, 7)}</span>
                </>
              ) : null}
            </p>
          </div>
          <span
            className={cn(
              "text-2xs shrink-0 rounded-full border px-2 py-0.5 font-medium",
              STATUS_TONE[change.status],
            )}
          >
            {CHANGE_STATUS_LABEL[change.status]}
          </span>
        </div>
        <ProgressTrack status={change.status} />
      </header>

      {change.summary ? (
        <Section title="What DevPilot found">
          <p className="text-ink-muted max-w-[68ch] text-sm leading-relaxed">{change.summary}</p>
        </Section>
      ) : null}

      {files.length > 0 ? (
        <Section title="Files affected">
          <ul className="space-y-1">
            {files.map((file) => (
              <li key={file.path} className="flex items-center gap-3 text-sm">
                <span className="text-ink min-w-0 flex-1 truncate font-mono text-xs">
                  {file.path}
                </span>
                <span className="text-2xs shrink-0 font-mono">
                  <span className="text-success">+{file.added}</span>{" "}
                  <span className="text-danger">−{file.removed}</span>
                </span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {change.status === "stale" ? (
        <Notice tone="warning" title="This proposal is out of date">
          The repository was re-indexed after this diff was produced, so it may no longer match the
          code. Investigate the change again.
        </Notice>
      ) : null}

      {change.error ? (
        <Notice tone="danger" title="The investigation reported a problem">
          {change.error}
        </Notice>
      ) : null}

      {change.investigation.length > 0 ? (
        <details className="group border-line border-t px-5 py-2.5">
          <summary className="text-ink-muted hover:text-ink flex cursor-pointer list-none items-center gap-1.5 text-sm transition-colors">
            <ChevronRight
              aria-hidden="true"
              className="size-3.5 transition-transform group-open:rotate-90"
              strokeWidth={2}
            />
            How DevPilot investigated
            <span className="text-ink-faint text-xs">
              · {change.investigation.length} step{change.investigation.length === 1 ? "" : "s"}
            </span>
          </summary>
          <ol className="mt-2 mb-1 space-y-1 pl-5">
            {change.investigation.map((step, index) => (
              <li key={index} className="text-ink-muted flex items-center gap-2 text-xs">
                <span className="text-ink font-mono">{step.tool}</span>
                {step.detail ? <span className="min-w-0 truncate">{step.detail}</span> : null}
                <span className="text-ink-faint ml-auto shrink-0 font-mono">
                  {step.duration_ms}ms
                </span>
              </li>
            ))}
          </ol>
        </details>
      ) : null}

      {change.diff ? (
        <details className="group border-line border-t" open={change.status === "proposed"}>
          <summary className="text-ink hover:bg-surface-hover flex cursor-pointer list-none items-center gap-1.5 px-5 py-2.5 text-sm font-medium transition-colors">
            <ChevronRight
              aria-hidden="true"
              className="size-3.5 transition-transform group-open:rotate-90"
              strokeWidth={2}
            />
            Proposed diff
            <span className="text-ink-faint text-xs font-normal">
              · {files.length} file{files.length === 1 ? "" : "s"}
            </span>
          </summary>
          <div className="border-line border-t">
            <DiffView diff={change.diff} />
          </div>
        </details>
      ) : null}

      {showExecution ? (
        <Section title="Pull request progress">
          <StageList
            label="Pull request progress"
            items={EXECUTION_STAGES.map((stage, index) => {
              const done = reached.has(stage.event);
              const firstMissing = EXECUTION_STAGES.findIndex(
                (candidate) => !reached.has(candidate.event),
              );
              return {
                label: stage.label,
                state: done
                  ? ("done" as const)
                  : change.status === "executing" && index === firstMissing
                    ? ("active" as const)
                    : ("pending" as const),
              };
            })}
          />
          {change.branch_name ? (
            <p className="text-ink-faint mt-2 font-mono text-xs">{change.branch_name}</p>
          ) : null}
          {change.execution_error ? (
            <p className="text-danger mt-2 text-sm">{change.execution_error}</p>
          ) : null}
        </Section>
      ) : null}

      <footer className="border-line bg-canvas/40 border-t px-5 py-3">
        {error ? (
          <p role="alert" className="text-danger mb-2 text-sm">
            {error}
          </p>
        ) : null}
        <Decision
          change={change}
          pending={pending}
          confirmReject={confirmReject}
          onApprove={() => void act("approve")}
          onAskReject={() => setConfirmReject(true)}
          onCancelReject={() => setConfirmReject(false)}
          onReject={() => void act("reject")}
          onExecute={() => void act("execute")}
          investigateAgainHref={investigateAgainHref}
        />
      </footer>
    </article>
  );
}

function Decision({
  change,
  pending,
  confirmReject,
  onApprove,
  onAskReject,
  onCancelReject,
  onReject,
  onExecute,
  investigateAgainHref,
}: {
  change: ProposedChange;
  pending: Pending;
  confirmReject: boolean;
  onApprove: () => void;
  onAskReject: () => void;
  onCancelReject: () => void;
  onReject: () => void;
  onExecute: () => void;
  investigateAgainHref: string;
}) {
  const busy = pending !== null;

  switch (change.status) {
    case "proposed":
      if (confirmReject) {
        return (
          <Row helper="A rejected proposal can't be approved later — you would investigate again.">
            <Button variant="danger" onClick={onReject} disabled={busy}>
              {pending === "reject" ? "Rejecting…" : "Reject change"}
            </Button>
            <Button variant="ghost" onClick={onCancelReject} disabled={busy}>
              Keep reviewing
            </Button>
          </Row>
        );
      }
      return (
        <Row helper="Approving records your decision. Nothing reaches GitHub until you create the pull request.">
          <Button variant="primary" onClick={onApprove} disabled={busy}>
            {pending === "approve" ? (
              <Loader2 aria-hidden="true" className="size-3.5 animate-spin" strokeWidth={2} />
            ) : (
              <Check aria-hidden="true" className="size-3.5" strokeWidth={2.25} />
            )}
            {pending === "approve" ? "Approving…" : "Approve change"}
          </Button>
          <Button variant="danger" onClick={onAskReject} disabled={busy}>
            Reject
          </Button>
        </Row>
      );

    case "approved":
    case "committed":
      return (
        <Row
          helper={
            change.status === "committed"
              ? "The branch and commit already exist — only opening the pull request failed."
              : "Creates a branch, commits this exact diff, and opens a pull request on GitHub."
          }
        >
          <Button variant="primary" onClick={onExecute} disabled={busy}>
            {pending === "execute" ? (
              <Loader2 aria-hidden="true" className="size-3.5 animate-spin" strokeWidth={2} />
            ) : (
              <GitPullRequest aria-hidden="true" className="size-3.5" strokeWidth={2} />
            )}
            {change.status === "committed"
              ? pending === "execute"
                ? "Retrying…"
                : "Retry pull request"
              : pending === "execute"
                ? "Creating branch & PR…"
                : "Create branch & PR"}
          </Button>
        </Row>
      );

    case "executing":
      return (
        <Row helper="GitHub is creating the branch, commit and pull request.">
          <Button variant="primary" disabled>
            <Loader2 aria-hidden="true" className="size-3.5 animate-spin" strokeWidth={2} />
            Creating pull request…
          </Button>
        </Row>
      );

    case "pr_created":
      return (
        <Row helper="This change is shipped. Merging happens on GitHub.">
          {change.pr_url ? (
            <a
              href={change.pr_url}
              target="_blank"
              rel="noreferrer"
              className={buttonStyles("primary", "md")}
            >
              View pull request #{change.pr_number}
              <ExternalLink aria-hidden="true" className="size-3.5" strokeWidth={2} />
              <span className="sr-only">(opens GitHub in a new tab)</span>
            </a>
          ) : null}
        </Row>
      );

    case "rejected":
      return (
        <Row helper="You rejected this change.">
          <ButtonLink href={investigateAgainHref} variant="secondary">
            Investigate again
          </ButtonLink>
        </Row>
      );

    case "stale":
    case "failed":
      return (
        <Row
          helper={
            change.status === "stale"
              ? "Out of date — investigate again against the current index."
              : "No applicable change was produced."
          }
        >
          <ButtonLink href={investigateAgainHref} variant="primary">
            Investigate again
          </ButtonLink>
        </Row>
      );
  }
}

function Row({ helper, children }: { helper: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      {children}
      <span className="text-ink-faint text-xs">{helper}</span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-line border-t px-5 py-3">
      <h4 className="text-ink-faint mb-1.5 text-xs font-semibold tracking-[0.08em] uppercase">
        {title}
      </h4>
      {children}
    </section>
  );
}

function Notice({
  tone,
  title,
  children,
}: {
  tone: "warning" | "danger";
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div role="alert" className="border-line flex gap-3 border-t px-5 py-3">
      <TriangleAlert
        aria-hidden="true"
        className={cn(
          "mt-0.5 size-4 shrink-0",
          tone === "warning" ? "text-warning" : "text-danger",
        )}
        strokeWidth={2}
      />
      <div>
        <p className="text-ink text-sm font-medium">{title}</p>
        <p className="text-ink-muted mt-0.5 text-sm">{children}</p>
      </div>
    </div>
  );
}

type TrackState = "done" | "current" | "upcoming" | "stopped";

function trackFor(status: ChangeStatus): [TrackState, TrackState, TrackState] {
  switch (status) {
    case "proposed":
      return ["done", "current", "upcoming"];
    case "approved":
    case "executing":
    case "committed":
      return ["done", "done", "current"];
    case "pr_created":
      return ["done", "done", "done"];
    case "rejected":
      return ["done", "stopped", "upcoming"];
    case "stale":
    case "failed":
      return ["stopped", "upcoming", "upcoming"];
  }
}

const TRACK_LABELS = ["Proposed", "Reviewed", "Shipped"] as const;

/** Proposed → Reviewed → Shipped, from the change's real status. */
function ProgressTrack({ status }: { status: ChangeStatus }) {
  const states = trackFor(status);

  return (
    <ol aria-label="Change progress" className="mt-3 flex items-center gap-2 text-xs">
      {TRACK_LABELS.map((label, index) => {
        const state = states[index] ?? "upcoming";
        return (
          <li key={label} className="flex items-center gap-2">
            {index > 0 ? (
              <span
                aria-hidden="true"
                className={cn("h-px w-6", state === "upcoming" ? "bg-line-strong" : "bg-ink-faint")}
              />
            ) : null}
            <span
              className={cn(
                "flex items-center gap-1.5",
                state === "done" && "text-ink-muted",
                state === "current" && "text-accent font-medium",
                state === "upcoming" && "text-ink-faint",
                state === "stopped" && "text-warning",
              )}
            >
              <span
                aria-hidden="true"
                className={cn(
                  "size-2 rounded-full border",
                  state === "done" && "bg-success border-success",
                  state === "current" && "bg-accent border-accent",
                  state === "upcoming" && "border-line-strong",
                  state === "stopped" && "bg-warning border-warning",
                )}
              />
              {label}
              <span className="sr-only">
                ({state === "stopped" ? "stopped" : state === "current" ? "current step" : state})
              </span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
