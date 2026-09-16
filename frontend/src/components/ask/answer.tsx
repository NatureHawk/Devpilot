"use client";

import { RichText } from "@/components/ask/rich-text";
import { cn } from "@/lib/cn";
import type { AskSource } from "@/lib/ask-stream";

/**
 * Renders answer text with citations as navigation into evidence.
 *
 * `[S1]` and grouped `[S1, S2]` become chips that name the file, symbol and
 * lines they point to. A label with no source behind it is dropped: it can't
 * lead anywhere, and bracketed labels are noise to a reader. The answer itself
 * is rendered as data (see RichText), never as markup.
 */
export function Answer({
  text,
  sources,
  onSelectSource,
  activeLabel,
}: {
  text: string;
  sources: AskSource[];
  onSelectSource: (label: string) => void;
  activeLabel: string | null;
}) {
  const byLabel = new Map(sources.map((source) => [source.label, source]));

  return (
    <RichText
      text={withKnownCitations(text, byLabel)}
      renderCitation={(labels, key) => (
        <span key={key} className="inline">
          {labels.flatMap((label) => {
            const source = byLabel.get(label);
            return source ? (
              <CitationChip
                key={label}
                source={source}
                active={activeLabel === label}
                onSelect={() => onSelectSource(label)}
              />
            ) : (
              []
            );
          })}
        </span>
      )}
    />
  );
}

const CITATION_GROUP = /[ \t]*\[(S\d+(?:\s*,\s*S\d+)*)\]/g;

/**
 * Keeps only citation labels that have a source, removing a citation (and the
 * space before it) entirely when none do — so "claimed [S7]." reads "claimed.".
 */
export function withKnownCitations(text: string, known: ReadonlyMap<string, unknown>): string {
  return text.replace(CITATION_GROUP, (match, group: string) => {
    const labels = group.split(/\s*,\s*/).filter((label) => known.has(label));
    if (labels.length === 0) return "";
    const lead = /^[ \t]*/.exec(match)?.[0] ?? "";
    return `${lead}[${labels.join(", ")}]`;
  });
}

export function basename(path: string): string {
  return path.split("/").pop() ?? path;
}

export function CitationChip({
  source,
  active,
  onSelect,
}: {
  source: AskSource;
  active: boolean;
  onSelect: () => void;
}) {
  const where = `${basename(source.file_path)}${source.symbol ? ` · ${source.symbol}` : ""} · ${source.start_line}–${source.end_line}`;

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={active}
      aria-label={`Show source ${source.label} — ${source.file_path}${source.symbol ? `, ${source.symbol}` : ""}, lines ${source.start_line}–${source.end_line}`}
      title={source.file_path}
      className={cn(
        "mx-0.5 inline-flex max-w-full items-center gap-1 rounded-md border px-1.5 align-[0.08em] text-[0.7rem] leading-[1.15rem] transition-colors",
        active
          ? "border-accent bg-accent-soft text-accent"
          : "border-line-strong bg-surface text-ink-muted hover:border-accent/60 hover:text-ink",
      )}
    >
      <span className="font-mono font-semibold">{source.label}</span>
      <span className="max-w-[15rem] truncate">{where}</span>
    </button>
  );
}
