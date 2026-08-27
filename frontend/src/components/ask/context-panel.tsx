"use client";

import { ChevronRight } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import type { AskSource } from "@/lib/ask-stream";

const CONTEXT_FIELDS = [
  { label: "File path", detail: "Where the excerpt lives in the repository." },
  { label: "Language", detail: "How the excerpt is parsed and highlighted." },
  { label: "Line range", detail: "The exact lines the answer drew on." },
  { label: "Relevance", detail: "How strongly the excerpt matched the question." },
] as const;

/**
 * Right-hand source panel.
 *
 * Shows the evidence an answer was built from. Empty until a question is asked,
 * because there is genuinely nothing to cite before then.
 */
export function ContextPanel({
  sources,
  activeLabel,
  onSelectSource,
}: {
  sources: AskSource[];
  activeLabel: string | null;
  onSelectSource: (label: string) => void;
}) {
  return (
    <aside
      aria-label="Repository context"
      className="border-line bg-surface hidden w-[300px] shrink-0 flex-col overflow-y-auto border-l xl:flex"
    >
      <header className="border-line flex h-10 shrink-0 items-center justify-between border-b px-4">
        <h2 className="text-ink text-xs font-medium">Repository context</h2>
        {sources.length > 0 ? (
          <span className="text-2xs text-ink-faint font-mono">{sources.length}</span>
        ) : null}
      </header>

      {sources.length === 0 ? (
        <div className="px-4 py-5">
          <p className="text-ink-muted text-xs leading-relaxed">
            Relevant files and code will appear here as DevPilot analyzes your question.
          </p>

          <dl className="border-line mt-6 space-y-4 border-t pt-5">
            {CONTEXT_FIELDS.map((field) => (
              <div key={field.label}>
                <dt className="text-2xs text-ink font-medium tracking-wide uppercase">
                  {field.label}
                </dt>
                <dd className="text-ink-muted mt-1 text-xs leading-relaxed">{field.detail}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : (
        <ol className="divide-line divide-y">
          {sources.map((source) => (
            <li key={source.chunk_id}>
              <SourceRow
                source={source}
                active={activeLabel === source.label}
                onSelect={() => onSelectSource(source.label)}
              />
            </li>
          ))}
        </ol>
      )}
    </aside>
  );
}

function SourceRow({
  source,
  active,
  onSelect,
}: {
  source: AskSource;
  active: boolean;
  onSelect: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className={cn(active && "bg-surface-hover")}>
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => {
          setExpanded((value) => !value);
          onSelect();
        }}
        className="hover:bg-surface-hover w-full px-4 py-3 text-left transition-colors"
      >
        <span className="flex items-center gap-2">
          <span className="text-2xs text-ink-faint shrink-0 font-mono">{source.label}</span>
          <ChevronRight
            aria-hidden="true"
            className={cn(
              "text-ink-faint size-3 shrink-0 transition-transform",
              expanded && "rotate-90",
            )}
            strokeWidth={1.75}
          />
          <span className="text-ink min-w-0 flex-1 truncate font-mono text-xs">
            {source.file_path}
          </span>
        </span>

        <span className="mt-1.5 flex items-center gap-2 pl-[1.65rem]">
          {source.symbol ? (
            <span className="text-ink-muted min-w-0 truncate text-xs">{source.symbol}</span>
          ) : null}
          <span className="text-2xs text-ink-faint shrink-0">
            {source.start_line}–{source.end_line}
          </span>
          <span className="ml-auto flex shrink-0 items-center gap-1.5">
            {source.language ? <Badge tone="neutral">{source.language}</Badge> : null}
            <span className="text-2xs text-ink font-mono">{source.score.toFixed(3)}</span>
          </span>
        </span>
      </button>

      {expanded ? (
        <div className="border-line bg-canvas border-t px-4 py-3">
          {/* Repository source is untrusted text: React escapes it and it is
              rendered as data inside <pre>, never as markup. */}
          <pre className="text-ink-muted overflow-x-auto font-mono text-2xs leading-relaxed">
            {source.content}
          </pre>
        </div>
      ) : null}
    </div>
  );
}
