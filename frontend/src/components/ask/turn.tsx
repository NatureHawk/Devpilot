"use client";

import { TriangleAlert } from "lucide-react";

import { Answer } from "@/components/ask/answer";
import { Evidence } from "@/components/ask/evidence";
import { splitAnswer } from "@/components/ask/rich-text";
import { SourceViewer } from "@/components/ask/source-viewer";
import { Button, ButtonLink } from "@/components/ui/button";
import { NextStep } from "@/components/ui/next-step";
import { StageList, type StageItem } from "@/components/ui/stage-list";
import type { AskSource } from "@/lib/ask-stream";
import { cn } from "@/lib/cn";

export type Turn = {
  question: string;
  answer: string;
  sources: AskSource[];
  status: "retrieving" | "generating" | "done" | "failed";
  error?: { code: string; message: string } | undefined;
  /** Loaded from history: citations exist, source text does not. */
  historical?: boolean;
};

function progress(turn: Turn): StageItem[] {
  return [
    {
      label:
        turn.status === "retrieving"
          ? "Finding the relevant code"
          : `Found ${turn.sources.length} relevant source${turn.sources.length === 1 ? "" : "s"}`,
      state: turn.status === "retrieving" ? "active" : "done",
    },
    {
      label: "Writing the answer",
      state:
        turn.status === "generating" ? "active" : turn.status === "retrieving" ? "pending" : "done",
    },
  ];
}

/**
 * One question and its result, structured like an engineer's write-up:
 * Question → Answer → What DevPilot found → What this means → Next step.
 */
export function TurnView({
  turn,
  index,
  isLast,
  activeLabel,
  onSelectSource,
  onCloseSource,
  showDetails,
  onToggleDetails,
  investigateHref,
  onAskFollowUp,
  onRetry,
}: {
  turn: Turn;
  index: number;
  isLast: boolean;
  activeLabel: string | null;
  onSelectSource: (label: string) => void;
  onCloseSource: () => void;
  showDetails: boolean;
  onToggleDetails: () => void;
  investigateHref: string;
  onAskFollowUp: () => void;
  onRetry: () => void;
}) {
  const { answer, meaning } = splitAnswer(turn.answer);
  const running = turn.status === "retrieving" || turn.status === "generating";
  const activeSource = activeLabel
    ? (turn.sources.find((source) => source.label === activeLabel) ?? null)
    : null;
  const questionId = `turn-${index}-question`;

  return (
    <article
      aria-labelledby={questionId}
      className={cn("animate-in", index > 0 && "border-line mt-12 border-t pt-10")}
    >
      <p className="text-ink-faint text-xs font-semibold tracking-[0.08em] uppercase">Question</p>
      <h2 id={questionId} className="text-ink mt-1 text-xl font-semibold tracking-tight">
        {turn.question}
      </h2>

      {running ? (
        <div role="status" aria-live="polite" className="mt-4">
          <StageList label="Answer progress" items={progress(turn)} />
        </div>
      ) : null}

      {answer ? (
        <section aria-labelledby={`turn-${index}-answer`} className="mt-6">
          <h3
            id={`turn-${index}-answer`}
            className="text-ink-faint text-xs font-semibold tracking-[0.08em] uppercase"
          >
            Answer
          </h3>
          <div className="text-ink mt-2 max-w-[68ch] text-[0.9375rem] leading-7">
            <Answer
              text={answer}
              sources={turn.sources}
              onSelectSource={onSelectSource}
              activeLabel={activeLabel}
            />
          </div>
        </section>
      ) : null}

      {turn.sources.length > 0 && turn.status !== "retrieving" ? (
        <div className="mt-8">
          <Evidence
            headingId={`turn-${index}-evidence`}
            sources={turn.sources}
            answerText={turn.answer}
            activeLabel={activeLabel}
            onSelect={onSelectSource}
            showDetails={showDetails}
            onToggleDetails={onToggleDetails}
          />
          {activeSource ? (
            <SourceViewer source={activeSource} onClose={onCloseSource} variant="inline" />
          ) : null}
        </div>
      ) : null}

      {meaning && turn.status === "done" ? (
        <section
          aria-labelledby={`turn-${index}-meaning`}
          className="border-accent/50 mt-8 max-w-[68ch] border-l-2 pl-4"
        >
          <h3
            id={`turn-${index}-meaning`}
            className="text-ink-faint text-xs font-semibold tracking-[0.08em] uppercase"
          >
            What this means
          </h3>
          <div className="text-ink mt-2 text-[0.9375rem] leading-7">
            <Answer
              text={meaning}
              sources={turn.sources}
              onSelectSource={onSelectSource}
              activeLabel={activeLabel}
            />
          </div>
        </section>
      ) : null}

      {turn.error ? (
        <div
          role="alert"
          className="border-danger/30 bg-danger/5 mt-6 flex gap-3 rounded-lg border px-4 py-3"
        >
          <TriangleAlert
            aria-hidden="true"
            className="text-danger mt-0.5 size-4 shrink-0"
            strokeWidth={2}
          />
          <div>
            <p className="text-ink text-sm font-medium">DevPilot couldn&apos;t answer this</p>
            <p className="text-ink-muted mt-1 text-sm">{turn.error.message}</p>
          </div>
        </div>
      ) : null}

      {isLast && !running ? (
        <NextStepForTurn
          turn={turn}
          investigateHref={investigateHref}
          onAskFollowUp={onAskFollowUp}
          onRetry={onRetry}
        />
      ) : null}
    </article>
  );
}

function NextStepForTurn({
  turn,
  investigateHref,
  onAskFollowUp,
  onRetry,
}: {
  turn: Turn;
  investigateHref: string;
  onAskFollowUp: () => void;
  onRetry: () => void;
}) {
  if (turn.status === "failed") {
    return (
      <NextStep
        className="mt-8"
        title="Try the question again"
        description="If it keeps failing, check that the API and its language model are available."
        action={
          <Button variant="primary" size="lg" forward onClick={onRetry}>
            Ask again
          </Button>
        }
      />
    );
  }

  if (turn.sources.length === 0) {
    return (
      <NextStep
        className="mt-8"
        title="Try a more specific question"
        description="Nothing in the repository matched closely. Name a file, a function, or the behaviour you're looking for — for example “Where is the database connection created?”"
        action={
          <Button variant="primary" size="lg" forward onClick={onAskFollowUp}>
            Ask another question
          </Button>
        }
      />
    );
  }

  return (
    <NextStep
      className="mt-8"
      title="Ready to change something here?"
      description="Describe the change you want. DevPilot reads these files in depth and proposes a diff for you to review — nothing reaches GitHub without your approval."
      action={
        <ButtonLink href={investigateHref} variant="primary" size="lg" forward>
          Investigate this code
        </ButtonLink>
      }
      secondary={
        <Button variant="ghost" onClick={onAskFollowUp}>
          Ask another question
        </Button>
      }
    />
  );
}
