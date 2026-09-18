"use client";

import { ArrowRight, CircleCheck, MessageSquareText, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";

import { proposeChangeAction } from "@/app/actions";
import { summarizeDiff } from "@/components/changes/diff-files";
import { StageHeading } from "@/components/layout/stage-heading";
import { CHANGE_STATUS_LABEL } from "@/components/repository/recent-work";
import { Button, ButtonLink } from "@/components/ui/button";
import { NextStep } from "@/components/ui/next-step";
import { StageList } from "@/components/ui/stage-list";
import type { ProposedChange } from "@/lib/api";
import { repositoryPath } from "@/lib/navigation";
import { formatElapsed, useElapsedSeconds } from "@/lib/use-elapsed";

/** What an investigation does. It runs as one request, so these happen together. */
const INVESTIGATION_STAGES = [
  "Understanding the request",
  "Retrieving the relevant code",
  "Reading the files and symbols involved",
  "Preparing a proposed change",
];

/**
 * Investigate: describe a change, let DevPilot propose a diff, then hand off to
 * review. Arriving from an answer, the question is pre-filled and the
 * conversation is linked to the investigation.
 */
export function InvestigateWorkspace({
  repositoryId,
  owner,
  name,
  disabledReason,
  blockedAction,
  initialRequest,
  conversationId,
  recent,
}: {
  repositoryId: string | null;
  owner: string;
  name: string;
  disabledReason: string | null;
  blockedAction: { label: string; href: string } | null;
  initialRequest: string;
  conversationId: string | null;
  recent: ProposedChange[];
}) {
  const textarea = useRef<HTMLTextAreaElement>(null);
  const [request, setRequest] = useState(initialRequest);
  const [running, setRunning] = useState(false);
  const [outcome, setOutcome] = useState<ProposedChange | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fromQuestion = Boolean(conversationId && initialRequest);
  const reviewPath = repositoryPath(owner, name, "review");

  const submit = async () => {
    if (!repositoryId || !request.trim() || running) return;
    setRunning(true);
    setError(null);
    const result = await proposeChangeAction(
      repositoryId,
      owner,
      name,
      request.trim(),
      conversationId ?? undefined,
    );
    setRunning(false);
    if (result.ok) setOutcome(result.data);
    else setError(result.error.message);
  };

  const editRequest = () => {
    setOutcome(null);
    requestAnimationFrame(() => textarea.current?.focus());
  };

  const startOver = () => {
    setOutcome(null);
    setRequest("");
    requestAnimationFrame(() => textarea.current?.focus());
  };

  return (
    <div className="space-y-10">
      <StageHeading
        step="investigate"
        title="Investigate a change"
        description={`Describe what should change in ${name}. DevPilot reads the relevant code and proposes a diff — you review it before anything reaches GitHub.`}
      />

      {disabledReason ? (
        <NextStep
          eyebrow="Before you can investigate"
          title={
            blockedAction ? `${blockedAction.label} first` : "Investigating isn't available yet"
          }
          description={disabledReason}
          action={
            blockedAction ? (
              <ButtonLink href={blockedAction.href} variant="primary" size="lg" forward>
                {blockedAction.label}
              </ButtonLink>
            ) : null
          }
        />
      ) : running ? (
        <Running request={request} />
      ) : outcome ? (
        <Result
          outcome={outcome}
          reviewHref={`${reviewPath}#change-${outcome.id}`}
          onEdit={editRequest}
          onStartOver={startOver}
        />
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          {fromQuestion ? (
            <p className="text-ink-muted mb-3 flex items-center gap-2 text-xs">
              <MessageSquareText
                aria-hidden="true"
                className="text-accent size-3.5"
                strokeWidth={2}
              />
              Started from your question in Ask — rewrite it as the change you want.
            </p>
          ) : null}
          <label htmlFor="change-request" className="text-ink block text-sm font-medium">
            What should change?
          </label>
          <textarea
            id="change-request"
            ref={textarea}
            rows={5}
            value={request}
            onChange={(event) => setRequest(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                void submit();
              }
            }}
            placeholder="Validate the order payload before it is written to the database."
            spellCheck={false}
            aria-describedby="change-request-hint"
            className="border-line-strong bg-canvas text-ink placeholder:text-ink-faint focus:border-accent focus:ring-accent/20 mt-2 w-full resize-y rounded-lg border px-3 py-2.5 text-sm leading-relaxed transition-colors focus:ring-2 focus:outline-none"
          />
          <p id="change-request-hint" className="text-ink-faint mt-1.5 text-xs">
            Name the behaviour you want, and where if you know it. Specific requests produce
            smaller, safer diffs.
          </p>

          {error ? (
            <p role="alert" className="text-danger mt-3 text-sm">
              {error}
            </p>
          ) : null}

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button type="submit" variant="primary" size="lg" forward disabled={!request.trim()}>
              Investigate
            </Button>
            <span className="text-ink-faint text-xs">
              Can take a minute. Nothing is applied without your review.
            </span>
          </div>

          {!fromQuestion && recent.length === 0 ? (
            <p className="text-ink-muted mt-8 text-sm">
              Not sure what to change yet?{" "}
              <Link
                href={repositoryPath(owner, name, "ask")}
                className="text-accent inline-flex items-center gap-1 font-medium hover:underline"
              >
                Ask a question first
                <ArrowRight aria-hidden="true" className="size-3.5" strokeWidth={2} />
              </Link>{" "}
              DevPilot will find the relevant area of the codebase.
            </p>
          ) : null}
        </form>
      )}

      {recent.length > 0 ? (
        <section aria-labelledby="earlier-investigations">
          <h2
            id="earlier-investigations"
            className="text-ink-faint text-xs font-medium tracking-wide uppercase"
          >
            Earlier investigations
          </h2>
          <ul className="border-line divide-line bg-surface mt-2 divide-y rounded-lg border">
            {recent.slice(0, 8).map((change) => (
              <li key={change.id}>
                <Link
                  href={`${reviewPath}#change-${change.id}`}
                  className="hover:bg-surface-hover flex items-center gap-3 px-4 py-2.5 transition-colors"
                >
                  <span className="text-ink min-w-0 flex-1 truncate text-sm">{change.request}</span>
                  <span className="text-2xs text-ink-faint shrink-0">
                    {CHANGE_STATUS_LABEL[change.status]}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function Running({ request }: { request: string }) {
  const elapsed = useElapsedSeconds(true);

  return (
    <section aria-busy="true" aria-labelledby="investigating-heading">
      <h2 id="investigating-heading" className="text-ink text-lg font-semibold">
        Investigating
      </h2>
      <blockquote className="border-line-strong text-ink-muted mt-2 border-l-2 pl-3 text-sm">
        {request}
      </blockquote>
      <div role="status" aria-live="polite" className="mt-5">
        <StageList
          label="Investigation"
          items={INVESTIGATION_STAGES.map((label) => ({ label, state: "running" as const }))}
        />
      </div>
      <p className="text-ink-faint mt-4 text-xs">
        Running for <span className="font-mono">{formatElapsed(elapsed)}</span>. These steps run in
        a single request and finish together — keep this page open.
      </p>
    </section>
  );
}

function Result({
  outcome,
  reviewHref,
  onEdit,
  onStartOver,
}: {
  outcome: ProposedChange;
  reviewHref: string;
  onEdit: () => void;
  onStartOver: () => void;
}) {
  const files = summarizeDiff(outcome.diff);
  const proposed = outcome.status === "proposed" && files.length > 0;
  const report = outcome.report ?? {};

  if (!proposed) {
    return (
      <section aria-labelledby="investigation-result">
        <div className="flex items-center gap-2">
          <TriangleAlert aria-hidden="true" className="text-warning size-5" strokeWidth={2} />
          <h2 id="investigation-result" className="text-ink text-lg font-semibold">
            {report.outcome === "insufficient_evidence"
              ? "Not enough evidence for a change"
              : report.outcome === "unsupported_request"
                ? "Not a change DevPilot can make"
                : "No change was proposed"}
          </h2>
        </div>
        <p className="text-ink-muted mt-2 max-w-2xl text-sm leading-relaxed">
          {outcome.error ??
            outcome.summary ??
            "DevPilot didn't find a change it could make safely."}
        </p>
        {report.summary && report.summary !== outcome.error ? (
          <p className="text-ink-muted mt-2 max-w-2xl text-sm leading-relaxed">{report.summary}</p>
        ) : null}
        {report.missing_information ? (
          <p className="text-ink-muted mt-2 max-w-2xl text-sm leading-relaxed">
            <span className="text-ink font-medium">Missing: </span>
            {report.missing_information}
          </p>
        ) : null}
        <NextStep
          className="mt-6"
          title="Refine the request"
          description="Be more specific about the behaviour and where it lives, then investigate again."
          action={
            <Button variant="primary" size="lg" forward onClick={onEdit}>
              Edit request
            </Button>
          }
        />
      </section>
    );
  }

  return (
    <section aria-labelledby="investigation-result">
      <div role="status" className="flex items-center gap-2">
        <CircleCheck aria-hidden="true" className="text-success size-5" strokeWidth={2} />
        <h2 id="investigation-result" className="text-ink text-lg font-semibold">
          Investigation complete
        </h2>
      </div>
      <p className="text-ink-muted mt-2 max-w-2xl text-sm leading-relaxed">
        DevPilot found the likely cause and prepared a change.
      </p>
      {outcome.summary ? (
        <p className="text-ink-muted mt-2 max-w-2xl text-sm leading-relaxed">{outcome.summary}</p>
      ) : null}
      {report.root_cause ? (
        <p className="text-ink-muted mt-2 max-w-2xl text-sm leading-relaxed">
          <span className="text-ink font-medium">Root cause: </span>
          {report.root_cause}
        </p>
      ) : null}
      <p className="text-ink-faint mt-2 text-xs">
        {files.length} file{files.length === 1 ? "" : "s"} changed:{" "}
        <span className="font-mono">{files.map((file) => file.path).join(", ")}</span>
        {report.evidence?.length
          ? ` · ${report.evidence.length} cited source${report.evidence.length === 1 ? "" : "s"}`
          : null}
        {report.confidence ? ` · ${report.confidence} confidence` : null}
      </p>

      <NextStep
        className="mt-6"
        title="Review the proposed change"
        description="Read the evidence and the diff, then approve or reject it. Nothing reaches GitHub until you do."
        action={
          <ButtonLink href={reviewHref} variant="primary" size="lg" forward>
            Review proposed change
          </ButtonLink>
        }
        secondary={
          <Button variant="ghost" onClick={onStartOver}>
            Investigate something else
          </Button>
        }
      />
    </section>
  );
}
