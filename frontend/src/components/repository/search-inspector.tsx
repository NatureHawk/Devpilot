"use client";

import { ChevronRight, Search } from "lucide-react";
import { useState, useTransition } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Panel, PanelHeader } from "@/components/ui/panel";
import { cn } from "@/lib/cn";
import type {
  RetrievalCandidate,
  RetrievalStrength,
  SearchResponse,
  SearchResult,
} from "@/lib/api";

/** Matches the shape server actions return: plain, serialisable, no status. */
type ActionError = { code: string; message: string };

const STRENGTH_TONE: Record<RetrievalStrength, "success" | "warning" | "neutral"> = {
  useful: "success",
  weak: "warning",
  none: "neutral",
};

/**
 * Retrieval inspector.
 *
 * An engineering instrument, not a chat surface: it answers "did retrieval
 * choose the right code?" before any model writes prose over it. It shows the
 * sources an answer would actually receive, and — collapsed — the semantic and
 * keyword candidate lists they were fused from. Scores are ranking signals and
 * are shown as such, never as confidence.
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

  const selected = new Set(response?.results.map((result) => result.chunk_id));

  return (
    <Panel>
      <PanelHeader
        title="Search this codebase"
        description="Semantic and keyword retrieval, fused. Shows exactly what an answer would receive — no answer is generated."
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

        {response ? <Summary response={response} /> : null}
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
        <ol className="divide-line divide-y" aria-label="Selected sources">
          {response.results.map((result, index) => (
            <li key={result.chunk_id}>
              <ResultRow result={result} label={index + 1} />
            </li>
          ))}
        </ol>
      ) : null}

      {response ? (
        <div className="border-line divide-line divide-y border-t">
          <CandidateList
            title="Semantic candidates"
            candidates={response.semantic_candidates}
            rankOf={(candidate) => candidate.semantic_rank}
            selected={selected}
          />
          <CandidateList
            title="Keyword candidates"
            candidates={response.lexical_candidates}
            rankOf={(candidate) => candidate.lexical_rank}
            selected={selected}
          />
        </div>
      ) : null}

      {!response && !error ? (
        <EmptyState
          title="Inspect what retrieval returns"
          description="Run a query to see which chunks an answer would receive, with their scores, exact line ranges, and the candidates they were chosen from."
        />
      ) : null}
    </Panel>
  );
}

function Summary({ response }: { response: SearchResponse }) {
  return (
    <div className="text-2xs text-ink-faint mt-2 space-y-1">
      <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
        <span>
          {response.results.length} of {response.max_sources} sources selected ·{" "}
          {response.context_chars.toLocaleString()} / {response.context_budget.toLocaleString()}{" "}
          chars
        </span>
        <Badge tone={STRENGTH_TONE[response.strength]}>evidence: {response.strength}</Badge>
      </p>
      <p>
        {response.semantic_candidates.length} semantic · {response.lexical_candidates.length}{" "}
        keyword candidates · {response.searched_chunks.toLocaleString()} chunks ·{" "}
        <span className="font-mono">{response.model}</span>
      </p>
      {!response.lexical_available ? (
        <p className="text-warning">
          Keyword search is unavailable for this index — re-index to enable it.
        </p>
      ) : null}
    </div>
  );
}

function qualifiedSymbol(candidate: RetrievalCandidate): string | null {
  return candidate.parent_symbol
    ? `${candidate.parent_symbol}.${candidate.symbol ?? ""}`
    : candidate.symbol;
}

function ResultRow({ result, label }: { result: SearchResult; label: number }) {
  const [expanded, setExpanded] = useState(false);
  const symbol = qualifiedSymbol(result);

  return (
    <div>
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        className="hover:bg-surface-hover flex w-full items-center gap-3 px-4 py-3 text-left transition-colors"
      >
        <span className="text-ink-faint w-5 shrink-0 font-mono text-xs">{label}</span>
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
          {result.exact_match ? <Badge tone="accent">{result.exact_match}</Badge> : null}
          {result.lexical_rank !== null ? (
            <Badge tone="neutral" className="font-mono">
              kw #{result.lexical_rank}
            </Badge>
          ) : null}
          {/* Fixed precision so scores line up and stay comparable by eye. */}
          <span className="text-ink font-mono text-xs" title="Semantic (cosine) score">
            {result.semantic_score.toFixed(3)}
          </span>
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

function CandidateList({
  title,
  candidates,
  rankOf,
  selected,
}: {
  title: string;
  candidates: RetrievalCandidate[];
  rankOf: (candidate: RetrievalCandidate) => number | null;
  selected: Set<string>;
}) {
  return (
    <details className="group">
      <summary className="text-ink-muted hover:bg-surface-hover flex cursor-pointer list-none items-center gap-2 px-4 py-2 text-xs transition-colors">
        <ChevronRight
          aria-hidden="true"
          className="text-ink-faint size-3.5 shrink-0 transition-transform group-open:rotate-90"
          strokeWidth={1.75}
        />
        {title} ({candidates.length})
      </summary>

      {candidates.length === 0 ? (
        <p className="text-2xs text-ink-faint px-4 pb-3">None.</p>
      ) : (
        <div className="overflow-x-auto px-4 pb-3">
          <table className="text-2xs w-full font-mono">
            <thead className="text-ink-faint text-left">
              <tr>
                <th className="py-1 pr-3 font-normal">#</th>
                <th className="py-1 pr-3 font-normal">location</th>
                <th className="py-1 pr-3 font-normal">symbol</th>
                <th className="py-1 pr-3 text-right font-normal">semantic</th>
                <th className="py-1 pr-3 text-right font-normal">fused</th>
                <th className="py-1 font-normal">
                  <span className="sr-only">status</span>
                </th>
              </tr>
            </thead>
            <tbody className="text-ink-muted">
              {candidates.map((candidate) => (
                <tr key={candidate.chunk_id} className={cn(candidate.low_value && "opacity-50")}>
                  <td className="py-0.5 pr-3">{rankOf(candidate)}</td>
                  <td className="py-0.5 pr-3 whitespace-nowrap">
                    {candidate.file_path}:{candidate.start_line}–{candidate.end_line}
                  </td>
                  <td className="py-0.5 pr-3">{qualifiedSymbol(candidate) ?? "—"}</td>
                  <td className="py-0.5 pr-3 text-right">{candidate.semantic_score.toFixed(3)}</td>
                  <td className="py-0.5 pr-3 text-right">{candidate.final_rank ?? "dup"}</td>
                  <td className="py-0.5 whitespace-nowrap">
                    {selected.has(candidate.chunk_id) ? (
                      <span className="text-success">selected</span>
                    ) : candidate.low_value ? (
                      "trivia"
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>
  );
}
