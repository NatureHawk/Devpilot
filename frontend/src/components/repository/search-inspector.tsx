"use client";

import { ChevronRight, Search } from "lucide-react";
import { useState, useTransition } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Panel, PanelHeader } from "@/components/ui/panel";
import { cn } from "@/lib/cn";
import type { SearchResponse, SearchResult } from "@/lib/api";

/** Matches the shape server actions return: plain, serialisable, no status. */
type ActionError = { code: string; message: string };

/**
 * Retrieval inspector.
 *
 * An engineering instrument, not a chat surface: it exists so retrieval quality
 * can be judged on its own before any model is asked to write prose over it.
 * Scores and ordering are shown deliberately — the question being answered here
 * is "did the right code come back, and in what order".
 */
export function SearchInspector({
  action,
}: {
  action: (
    query: string,
  ) => Promise<{ ok: true; data: SearchResponse } | { ok: false; error: ActionError }>;
}) {
  const [query, setQuery] = useState("");
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<ActionError | null>(null);
  const [pending, startTransition] = useTransition();

  const submit = () => {
    const trimmed = query.trim();
    if (!trimmed || pending) return;

    startTransition(async () => {
      const outcome = await action(trimmed);
      if (outcome.ok) {
        setResponse(outcome.data);
        setError(null);
      } else {
        setError(outcome.error);
        setResponse(null);
      }
    });
  };

  return (
    <Panel>
      <PanelHeader
        title="Search this codebase"
        description="Semantic retrieval over indexed chunks. No answer is generated — these are the raw matches."
      />

      <div className="border-line border-b p-4">
        <div className="flex gap-2">
          <label htmlFor="repository-search" className="sr-only">
            Search query
          </label>
          <input
            id="repository-search"
            type="text"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") submit();
            }}
            placeholder="Where is authentication handled?"
            spellCheck={false}
            className="border-line-strong bg-canvas text-ink placeholder:text-ink-faint focus:border-accent h-8 min-w-0 flex-1 rounded-md border px-3 text-sm transition-colors focus:outline-none"
          />
          <Button
            variant="primary"
            onClick={submit}
            disabled={pending || query.trim().length === 0}
          >
            <Search aria-hidden="true" className="size-3.5" strokeWidth={2} />
            {pending ? "Searching…" : "Search"}
          </Button>
        </div>

        {response ? (
          <p className="text-2xs text-ink-faint mt-2">
            {response.results.length} of {response.searched_chunks.toLocaleString()} chunks ·{" "}
            <span className="font-mono">{response.model}</span>
          </p>
        ) : null}
      </div>

      {error ? (
        <div role="alert" className="px-4 py-4">
          <p className="text-ink text-sm font-medium">Search failed</p>
          <p className="text-ink-muted mt-1 text-sm">{error.message}</p>
          <p className="text-2xs text-ink-faint mt-2 font-mono">{error.code}</p>
        </div>
      ) : null}

      {response && response.results.length === 0 ? (
        <EmptyState
          title="No matches"
          description="Nothing in this repository's index was close to that query. Try naming a symbol, a file, or the behaviour you are looking for."
        />
      ) : null}

      {response && response.results.length > 0 ? (
        <ol className="divide-line divide-y">
          {response.results.map((result, index) => (
            <li key={result.chunk_id}>
              <ResultRow result={result} rank={index + 1} />
            </li>
          ))}
        </ol>
      ) : null}

      {!response && !error ? (
        <EmptyState
          title="Inspect what retrieval returns"
          description="Run a query to see which chunks the index considers closest, with their similarity scores and exact line ranges."
        />
      ) : null}
    </Panel>
  );
}

function ResultRow({ result, rank }: { result: SearchResult; rank: number }) {
  const [expanded, setExpanded] = useState(false);
  const symbol = result.parent_symbol
    ? `${result.parent_symbol}.${result.symbol ?? ""}`
    : result.symbol;

  return (
    <div>
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        className="hover:bg-surface-hover flex w-full items-center gap-3 px-4 py-3 text-left transition-colors"
      >
        <span className="text-ink-faint w-5 shrink-0 font-mono text-xs">{rank}</span>
        <ChevronRight
          aria-hidden="true"
          className={cn(
            "text-ink-faint size-3.5 shrink-0 transition-transform",
            expanded && "rotate-90",
          )}
          strokeWidth={1.75}
        />

        <span className="min-w-0 flex-1">
          <span className="text-ink block truncate font-mono text-xs">{result.file_path}</span>
          <span className="text-ink-muted mt-0.5 block truncate text-xs">
            {symbol ? <span className="text-ink">{symbol}</span> : null}
            {symbol ? " · " : null}
            {result.chunk_type} · lines {result.start_line}–{result.end_line}
          </span>
        </span>

        <span className="flex shrink-0 items-center gap-2">
          <Badge tone="neutral">{result.language}</Badge>
          {/* Fixed precision so scores line up and stay comparable by eye. */}
          <span className="text-ink font-mono text-xs">{result.score.toFixed(3)}</span>
        </span>
      </button>

      {expanded ? (
        <div className="border-line bg-canvas border-t px-4 py-3">
          {/* Repository source is untrusted text. React escapes it, and it is
              rendered inside <pre> as data — never as markup or instructions. */}
          <pre className="text-ink-muted overflow-x-auto font-mono text-xs leading-relaxed">
            {result.content}
          </pre>
        </div>
      ) : null}
    </div>
  );
}
