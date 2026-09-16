"use client";

import { CircleCheck, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { indexRepositoryAction, type IndexOutcome } from "@/app/actions";
import { Button, ButtonLink } from "@/components/ui/button";
import { NextStep } from "@/components/ui/next-step";
import { StageList } from "@/components/ui/stage-list";
import type { IndexingStatus, LanguageCount } from "@/lib/api";
import { repositoryPath } from "@/lib/navigation";
import { formatElapsed, useElapsedSeconds } from "@/lib/use-elapsed";
import { askNextAction, type NextAction } from "@/lib/workflow";

export type IndexSummary = {
  status: IndexingStatus;
  commitSha: string | null;
  indexedAt: string | null;
  fileCount: number;
  parsedFileCount: number;
  chunkCount: number;
  error: string | null;
};

/** What an indexing run does. It runs as one request, so these happen together. */
const INDEXING_STAGES = [
  "Reading the repository tree from GitHub",
  "Downloading and parsing source files",
  "Generating embeddings for search",
  "Saving the search index",
];

/**
 * The repository's index state, and always the step that follows from it.
 *
 * Indexing is synchronous, so there is no progress stream. The running state
 * lists what the run does and how long it has been going — both true — rather
 * than ticking through stages it cannot observe.
 */
export function IndexPanel({
  repositoryId,
  owner,
  name,
  summary,
  next,
  languages = [],
}: {
  repositoryId: string;
  owner: string;
  name: string;
  summary: IndexSummary;
  /** The workflow's next action from server state; used once indexed. */
  next?: NextAction | undefined;
  languages?: LanguageCount[];
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

  if (running) return <IndexingInProgress name={name} />;

  const failureMessage = error ?? (!outcome && summary.status === "failed" ? summary.error : null);
  if (failureMessage) {
    return <IndexingFailed message={failureMessage} summary={summary} onRetry={run} />;
  }

  if (outcome || summary.status === "indexed") {
    const current: IndexSummary = outcome
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
      : summary;

    // A run that just finished always leads to asking, whatever the server's
    // (pre-run) view of the workflow was.
    const nextAction: NextAction =
      outcome || !next || next.stage === "repository" ? askNextAction(name) : next;

    return (
      <IndexingComplete
        name={name}
        summary={current}
        justCompleted={outcome !== null}
        partial={outcome ? !outcome.complete : false}
        languages={languages}
        next={nextAction}
        href={repositoryPath(owner, name, nextAction.segment)}
        onReindex={run}
      />
    );
  }

  if (summary.status === "indexing") {
    return (
      <section>
        <StatusHeading>Indexing is in progress</StatusHeading>
        <p className="text-ink-muted mt-2 text-sm">
          A run started elsewhere is still going. Refresh this page to see when it finishes.
        </p>
      </section>
    );
  }

  return <NotIndexed name={name} onIndex={run} />;
}

function StatusHeading({ children }: { children: React.ReactNode }) {
  return <h1 className="text-ink text-2xl font-semibold tracking-tight">{children}</h1>;
}

function NotIndexed({ name, onIndex }: { name: string; onIndex: () => void | Promise<void> }) {
  return (
    <section>
      <StatusHeading>{name} hasn&apos;t been indexed yet</StatusHeading>
      <p className="text-ink-muted mt-2 max-w-2xl text-sm leading-relaxed">
        Indexing reads the default branch and prepares its source for code-aware search. Until then,
        DevPilot has nothing to answer questions from.
      </p>

      <NextStep
        className="mt-6"
        title={`Index ${name}`}
        description="Reads the repository tree, parses source files into functions and classes, and builds the search index. Nothing is written to GitHub."
        action={
          <Button variant="primary" size="lg" forward onClick={() => void onIndex()}>
            Index repository
          </Button>
        }
        secondary={
          <span className="text-ink-faint text-xs">Keep this page open while it runs.</span>
        }
      />
    </section>
  );
}

function IndexingInProgress({ name }: { name: string }) {
  const elapsed = useElapsedSeconds(true);

  return (
    <section aria-busy="true">
      <StatusHeading>Indexing {name}</StatusHeading>
      <div role="status" aria-live="polite" className="mt-5">
        <StageList
          label="Indexing"
          items={INDEXING_STAGES.map((label) => ({ label, state: "running" as const }))}
        />
      </div>
      <p className="text-ink-faint mt-4 text-xs">
        Running for <span className="font-mono">{formatElapsed(elapsed)}</span>. These steps run in
        a single request and finish together — keep this page open.
      </p>
    </section>
  );
}

function IndexingComplete({
  name,
  summary,
  justCompleted,
  partial,
  languages,
  next,
  href,
  onReindex,
}: {
  name: string;
  summary: IndexSummary;
  justCompleted: boolean;
  partial: boolean;
  languages: LanguageCount[];
  next: NextAction;
  href: string;
  onReindex: () => void | Promise<void>;
}) {
  const languageLine = languages
    .slice(0, 4)
    .map((entry) => `${entry.language} ${entry.file_count}`)
    .join(" · ");

  return (
    <section>
      <div role={justCompleted ? "status" : undefined} className="flex items-center gap-2">
        <CircleCheck aria-hidden="true" className="text-success size-5" strokeWidth={2} />
        <StatusHeading>{justCompleted ? `${name} is ready` : `${name} is indexed`}</StatusHeading>
      </div>

      <dl className="text-ink-muted mt-3 flex flex-wrap gap-x-5 gap-y-1 text-sm">
        <div className="flex gap-1.5">
          <dt className="sr-only">Files</dt>
          <dd>
            <span className="text-ink font-medium">{summary.fileCount.toLocaleString()}</span> files
            {summary.parsedFileCount !== summary.fileCount
              ? ` (${summary.parsedFileCount.toLocaleString()} parsed)`
              : ""}
          </dd>
        </div>
        <div className="flex gap-1.5">
          <dt className="sr-only">Searchable chunks</dt>
          <dd>
            <span className="text-ink font-medium">{summary.chunkCount.toLocaleString()}</span>{" "}
            searchable chunks
          </dd>
        </div>
        {summary.commitSha ? (
          <div className="flex gap-1.5">
            <dt className="sr-only">Commit</dt>
            <dd>
              at <span className="text-ink font-mono">{summary.commitSha.slice(0, 7)}</span>
            </dd>
          </div>
        ) : null}
        {summary.indexedAt ? (
          <div className="flex gap-1.5">
            <dt className="sr-only">Indexed</dt>
            <dd>
              <time dateTime={summary.indexedAt} suppressHydrationWarning>
                {new Date(summary.indexedAt).toLocaleString()}
              </time>
            </dd>
          </div>
        ) : null}
      </dl>
      {languageLine ? <p className="text-ink-faint mt-1 text-xs">{languageLine}</p> : null}

      {partial ? (
        <p className="text-warning mt-3 flex items-center gap-1.5 text-sm">
          <TriangleAlert aria-hidden="true" className="size-4" strokeWidth={2} />
          Partially parsed — some supported files are stored as plain text.
        </p>
      ) : null}

      <NextStep
        className="mt-6"
        title={next.title}
        description={next.description}
        action={
          <ButtonLink href={href} variant="primary" size="lg" forward>
            {next.label}
          </ButtonLink>
        }
      />

      <div className="mt-3 flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={() => void onReindex()}>
          Re-index
        </Button>
        <span className="text-ink-faint text-xs">
          Replaces the index with the current default branch.
        </span>
      </div>
    </section>
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
    <section>
      <div className="flex items-center gap-2">
        <TriangleAlert aria-hidden="true" className="text-danger size-5" strokeWidth={2} />
        <StatusHeading>Indexing failed</StatusHeading>
      </div>
      <p role="alert" className="text-ink-muted mt-2 max-w-2xl text-sm leading-relaxed">
        {message}
      </p>
      {hasPreviousIndex ? (
        <p className="text-ink-faint mt-2 text-xs">
          The previous index is intact — {summary.fileCount.toLocaleString()} files and{" "}
          {summary.chunkCount.toLocaleString()} chunks are still searchable.
        </p>
      ) : null}

      <NextStep
        className="mt-6"
        title="Try indexing again"
        description="Most failures are temporary — a GitHub rate limit or a provider timeout."
        action={
          <Button variant="primary" size="lg" onClick={() => void onRetry()}>
            Retry indexing
          </Button>
        }
      />
    </section>
  );
}
