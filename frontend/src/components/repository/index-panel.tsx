"use client";

import { AlertTriangle, CheckCircle2, Loader2 } from "lucide-react";
import { useState } from "react";

import { indexRepositoryAction, type IndexOutcome } from "@/app/actions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import type { IndexingStatus } from "@/lib/api";

export type IndexSummary = {
  status: IndexingStatus;
  commitSha: string | null;
  indexedAt: string | null;
  fileCount: number;
  parsedFileCount: number;
  chunkCount: number;
  error: string | null;
};

/**
 * The indexing control and its real state.
 *
 * Indexing runs synchronously inside the request, so there is no progress
 * stream to read. Rather than inventing a percentage, the pending state says
 * what is happening and the counts appear when the run actually returns them.
 */
export function IndexPanel({
  repositoryId,
  owner,
  name,
  summary,
}: {
  repositoryId: string;
  owner: string;
  name: string;
  summary: IndexSummary;
}) {
  const [running, setRunning] = useState(false);
  const [outcome, setOutcome] = useState<IndexOutcome | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setRunning(true);
    setError(null);
    setOutcome(null);
    try {
      const result = await indexRepositoryAction(repositoryId, owner, name);
      if (result.ok) setOutcome(result.data);
      else setError(result.error.message);
    } finally {
      setRunning(false);
    }
  };

  if (running) return <IndexingInProgress />;

  const status = outcome ? "indexed" : summary.status;
  const failureMessage = error ?? (status === "failed" ? summary.error : null);

  if (failureMessage) {
    return <IndexingFailed message={failureMessage} summary={summary} onRetry={run} />;
  }

  if (status === "indexed") {
    return (
      <IndexingComplete
        summary={
          outcome
            ? {
                ...summary,
                status: "indexed",
                commitSha: outcome.commitSha,
                indexedAt: new Date().toISOString(),
                fileCount: outcome.filesIndexed,
                parsedFileCount: outcome.filesParsed,
                chunkCount: outcome.chunksCreated,
                error: null,
              }
            : summary
        }
        partial={outcome ? !outcome.complete : false}
        onReindex={run}
      />
    );
  }

  return <NotIndexed onIndex={run} />;
}

function NotIndexed({ onIndex }: { onIndex: () => void | Promise<void> }) {
  return (
    <Panel>
      <div className="px-6 pt-7 pb-6">
        <h2 className="text-ink text-lg font-medium tracking-tight">
          This repository hasn&apos;t been indexed yet
        </h2>
        <p className="text-ink-muted mt-2 max-w-2xl text-sm leading-relaxed">
          Analyze the repository structure and prepare its source for code-aware search.
        </p>
      </div>

      <ul className="divide-line border-line grid grid-cols-1 divide-y border-t sm:grid-cols-2 sm:divide-x sm:divide-y-0">
        <li className="px-6 py-4">
          <h3 className="text-ink text-sm font-medium">Read and parse</h3>
          <p className="text-ink-muted mt-1 text-xs leading-relaxed">
            Walk the default branch, read source files, and parse supported languages into their
            functions and classes.
          </p>
        </li>
        <li className="px-6 py-4">
          <h3 className="text-ink text-sm font-medium">Store structured code</h3>
          <p className="text-ink-muted mt-1 text-xs leading-relaxed">
            Keep each symbol with its file path and line range, ready for retrieval to search.
          </p>
        </li>
      </ul>

      <div className="border-line flex flex-wrap items-center gap-3 border-t px-6 py-4">
        <Button variant="primary" size="sm" onClick={() => void onIndex()}>
          Index repository
        </Button>
        <span className="text-ink-faint text-xs">
          Runs now and holds the page until it finishes.
        </span>
      </div>
    </Panel>
  );
}

function IndexingInProgress() {
  return (
    <Panel>
      <div className="px-6 py-8">
        <div className="flex items-center gap-2.5">
          <Loader2 aria-hidden="true" className="text-accent size-4 animate-spin" />
          <h2 className="text-ink text-lg font-medium tracking-tight">Indexing repository…</h2>
        </div>
        <p className="text-ink-muted mt-2 max-w-2xl text-sm leading-relaxed">
          DevPilot is reading the repository tree, downloading source files, and parsing them into
          chunks. This runs in one request, so counts appear when it completes rather than
          incrementing as it goes.
        </p>
        <p className="text-ink-faint mt-3 text-xs">Leaving this page cancels the run.</p>
      </div>
    </Panel>
  );
}

function IndexingComplete({
  summary,
  partial,
  onReindex,
}: {
  summary: IndexSummary;
  partial: boolean;
  onReindex: () => void | Promise<void>;
}) {
  const stats: [string, string][] = [
    ["Files indexed", summary.fileCount.toLocaleString()],
    ["Files parsed", summary.parsedFileCount.toLocaleString()],
    ["Chunks created", summary.chunkCount.toLocaleString()],
    ["Commit", summary.commitSha ? summary.commitSha.slice(0, 7) : "—"],
  ];

  return (
    <Panel>
      <div className="border-line flex items-start justify-between gap-4 border-b px-6 py-5">
        <div>
          <div className="flex items-center gap-2">
            <CheckCircle2 aria-hidden="true" className="text-success size-4" strokeWidth={1.75} />
            <h2 className="text-ink text-lg font-medium tracking-tight">Repository indexed</h2>
          </div>
          <p className="text-ink-muted mt-1.5 text-sm">
            {summary.indexedAt ? (
              <>
                Last indexed{" "}
                <time dateTime={summary.indexedAt} suppressHydrationWarning>
                  {new Date(summary.indexedAt).toLocaleString()}
                </time>
              </>
            ) : (
              "Indexed."
            )}
          </p>
        </div>
        {partial ? <Badge tone="warning">Partially parsed</Badge> : null}
      </div>

      <dl className="divide-line grid grid-cols-2 divide-y sm:grid-cols-4 sm:divide-x sm:divide-y-0">
        {stats.map(([label, value]) => (
          <div key={label} className="px-5 py-4">
            <dt className="text-2xs text-ink-faint tracking-wide uppercase">{label}</dt>
            <dd className="text-ink mt-1 font-mono text-sm">{value}</dd>
          </div>
        ))}
      </dl>

      <div className="border-line flex flex-wrap items-center gap-3 border-t px-6 py-4">
        <Button variant="secondary" size="sm" onClick={() => void onReindex()}>
          Re-index
        </Button>
        <span className="text-ink-faint text-xs">
          {partial
            ? "Some supported files could not be parsed; they are stored as text."
            : "Re-indexing replaces the stored snapshot with the current default branch."}
        </span>
      </div>
    </Panel>
  );
}

function IndexingFailed({
  message,
  summary,
  onRetry,
}: {
  message: string;
  summary: IndexSummary;
  onRetry: () => void | Promise<void>;
}) {
  // A failed run never deletes a previous index, so say so rather than letting
  // the failure imply the repository is now unsearchable.
  const hasPreviousIndex = summary.fileCount > 0;

  return (
    <Panel>
      <div className="px-6 pt-6 pb-5">
        <div className="flex items-center gap-2">
          <AlertTriangle aria-hidden="true" className="text-danger size-4" strokeWidth={1.75} />
          <h2 className="text-ink text-lg font-medium tracking-tight">Indexing failed</h2>
        </div>
        <p className="text-ink-muted mt-2 max-w-2xl text-sm leading-relaxed">{message}</p>
        {hasPreviousIndex ? (
          <p className="text-ink-faint mt-3 text-xs">
            The previous index is intact — {summary.fileCount.toLocaleString()} files and{" "}
            {summary.chunkCount.toLocaleString()} chunks are still searchable.
          </p>
        ) : null}
      </div>

      <div className="border-line border-t px-6 py-4">
        <Button variant="primary" size="sm" onClick={() => void onRetry()}>
          Retry indexing
        </Button>
      </div>
    </Panel>
  );
}
