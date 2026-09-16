"use client";

import { ChevronRight, Code2 } from "lucide-react";

import { basename } from "@/components/ask/answer";
import { cn } from "@/lib/cn";
import type { AskSource } from "@/lib/ask-stream";

const CITATION = /\[(S\d+(?:\s*,\s*S\d+)*)\]/g;

/** Labels the answer actually cites. */
export function citedLabels(text: string): Set<string> {
  const labels = new Set<string>();
  for (const match of text.matchAll(CITATION)) {
    for (const label of (match[1] ?? "").split(/\s*,\s*/)) labels.add(label);
  }
  return labels;
}

/** The first line of a chunk that says what it is, skipping blanks, comments and decorators. */
function previewLine(content: string): string | null {
  const line = content
    .split("\n")
    .map((candidate) => candidate.trim())
    .find((candidate) => candidate && !/^(#|\/\/|\/\*|\*|@|\{\/\*)/.test(candidate));
  if (!line) return null;
  return line.length > 96 ? `${line.slice(0, 95)}…` : line;
}

/**
 * "What DevPilot found": the retrieved evidence as compact rows.
 *
 * Each row names the symbol, file and lines, and opens the code. Cited sources
 * come first — they are the ones the answer relies on. Retrieval scores and
 * chunk metadata are diagnostics: available, but behind a toggle.
 */
export function Evidence({
  sources,
  answerText,
  activeLabel,
  onSelect,
  showDetails,
  onToggleDetails,
  headingId,
}: {
  sources: AskSource[];
  answerText: string;
  activeLabel: string | null;
  onSelect: (label: string) => void;
  showDetails: boolean;
  onToggleDetails: () => void;
  headingId: string;
}) {
  const cited = citedLabels(answerText);
  const files = new Set(sources.map((source) => source.file_path)).size;
  const ordered = [...sources].sort(
    (a, b) => Number(cited.has(b.label)) - Number(cited.has(a.label)),
  );
  const diagnosticsId = `${headingId}-diagnostics`;

  return (
    <section aria-labelledby={headingId}>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <h3
          id={headingId}
          className="text-ink-faint text-xs font-semibold tracking-[0.08em] uppercase"
        >
          What DevPilot found
        </h3>
        <span className="text-ink-faint text-xs">
          {sources.length} relevant source{sources.length === 1 ? "" : "s"} · {files} file
          {files === 1 ? "" : "s"}
        </span>
      </div>

      <ul className="border-line bg-surface divide-line mt-2.5 divide-y rounded-lg border">
        {ordered.map((source) => {
          const active = activeLabel === source.label;
          return (
            <li
              key={`${source.label}-${source.chunk_id}`}
              className={cn(
                "flex items-center gap-3 px-3 py-2 transition-colors first:rounded-t-lg last:rounded-b-lg",
                active && "bg-accent-soft",
              )}
            >
              <span
                className={cn(
                  "text-2xs shrink-0 rounded border px-1 font-mono",
                  active ? "border-accent/50 text-accent" : "border-line-strong text-ink-faint",
                )}
              >
                {source.label}
              </span>
              <div className="min-w-0 flex-1">
                <p className="flex items-baseline gap-2">
                  <span className="text-ink truncate text-sm font-medium">
                    {source.symbol ?? basename(source.file_path)}
                  </span>
                  {cited.has(source.label) ? (
                    <span className="text-2xs text-accent shrink-0">Cited</span>
                  ) : null}
                </p>
                <p className="text-ink-faint truncate text-xs" title={source.file_path}>
                  <span className="font-mono">{source.file_path}</span> · lines {source.start_line}–
                  {source.end_line}
                </p>
              </div>
              <button
                type="button"
                onClick={() => onSelect(source.label)}
                aria-expanded={active}
                className={cn(
                  "inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border px-2 text-xs font-medium transition-colors",
                  active
                    ? "border-accent/50 text-accent"
                    : "border-line-strong text-ink-muted hover:border-ink-faint hover:text-ink",
                )}
              >
                <Code2 aria-hidden="true" className="size-3.5" strokeWidth={2} />
                {active ? "Hide code" : "View code"}
                <span className="sr-only"> for {source.label}</span>
              </button>
            </li>
          );
        })}
      </ul>

      <button
        type="button"
        onClick={onToggleDetails}
        aria-expanded={showDetails}
        aria-controls={diagnosticsId}
        className="text-ink-faint hover:text-ink mt-2 inline-flex items-center gap-1 rounded text-xs transition-colors"
      >
        <ChevronRight
          aria-hidden="true"
          className={cn("size-3 transition-transform", showDetails && "rotate-90")}
          strokeWidth={2}
        />
        Retrieval diagnostics
      </button>
      {showDetails ? (
        <div
          id={diagnosticsId}
          className="border-line bg-canvas mt-2 overflow-x-auto rounded-md border px-3 py-2"
        >
          <ul className="text-2xs text-ink-faint space-y-1 font-mono">
            {sources.map((source) => {
              const preview = previewLine(source.content);
              return (
                <li key={`${source.label}-${source.chunk_id}`} className="whitespace-nowrap">
                  <span className="text-ink-muted">{source.label}</span> semantic{" "}
                  {source.score.toFixed(3)}
                  {source.chunk_type ? ` · ${source.chunk_type}` : ""}
                  {source.language ? ` · ${source.language}` : ""}
                  {preview ? <span className="text-ink-muted"> · {preview}</span> : null}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
