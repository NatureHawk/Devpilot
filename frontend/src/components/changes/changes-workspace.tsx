"use client";

import { Check, Loader2, Wrench, X } from "lucide-react";
import { useState, useTransition } from "react";

import { DiffView } from "@/components/changes/diff-view";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Panel, PanelHeader } from "@/components/ui/panel";
import { proposeChangeAction, reviewChangeAction } from "@/app/actions";
import type { ChangeStatus, ProposedChange } from "@/lib/api";

type ActionError = { code: string; message: string };

const STATUS_TONE: Record<ChangeStatus, "neutral" | "accent" | "success" | "warning" | "danger"> = {
  proposed: "accent",
  approved: "success",
  rejected: "neutral",
  stale: "warning",
  failed: "danger",
};

const STATUS_LABEL: Record<ChangeStatus, string> = {
  proposed: "Awaiting review",
  approved: "Approved",
  rejected: "Rejected",
  stale: "Stale",
  failed: "Failed",
};

/**
 * The change-review workspace: request, investigation, proposal, diff, decision.
 *
 * Staged deliberately — a diff arrives with the evidence behind it, and a
 * decision is a separate, explicit act. There is no deploy step: approval is
 * recorded in DevPilot and nothing is written to GitHub.
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
              <p className="text-ink text-sm font-medium">Could not propose a change</p>
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
          <ChangeCard key={change.id} change={change} onReview={review} busy={pending} />
        ))
      )}
    </div>
  );
}

function ChangeCard({
  change,
  onReview,
  busy,
}: {
  change: ProposedChange;
  onReview: (changeId: string, decision: "approve" | "reject") => void;
  busy: boolean;
}) {
  const reviewable = change.status === "proposed";

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

      <div className="border-line flex items-center gap-2 border-t px-4 py-3">
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
          Approval is recorded in DevPilot. Nothing is committed or pushed.
        </span>
      </div>
    </Panel>
  );
}
