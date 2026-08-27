"use client";

import { cn } from "@/lib/cn";
import type { AskSource } from "@/lib/ask-stream";

/**
 * Renders an answer with its citations turned into clickable references.
 *
 * The model writes `[S1]` inline. Those become buttons that select the source,
 * which is what makes a claim traceable to code rather than to prose about
 * code. Everything else is rendered as plain text — the answer is never treated
 * as markup.
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
  const known = new Set(sources.map((source) => source.label));

  return (
    <div className="text-ink text-sm leading-relaxed">
      {text.split("\n").map((line, lineIndex) => (
        <p key={lineIndex} className={cn(line.trim() === "" && "h-3")}>
          {renderLine(line, known, onSelectSource, activeLabel)}
        </p>
      ))}
    </div>
  );
}

/** Splits a line on citation labels, leaving the rest as text. */
function renderLine(
  line: string,
  known: Set<string>,
  onSelectSource: (label: string) => void,
  activeLabel: string | null,
) {
  const pattern = /\[(S\d+)\]/g;
  const nodes: React.ReactNode[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(line)) !== null) {
    const label = match[1]!;
    if (match.index > cursor) nodes.push(line.slice(cursor, match.index));

    // A label the model invented has no source behind it, so it stays as
    // literal text rather than becoming a link to nothing.
    if (known.has(label)) {
      nodes.push(
        <button
          key={`${label}-${match.index}`}
          type="button"
          onClick={() => onSelectSource(label)}
          aria-label={`Show source ${label}`}
          className={cn(
            "mx-0.5 inline-flex h-4 items-center rounded border px-1 align-baseline font-mono text-2xs transition-colors",
            activeLabel === label
              ? "border-accent text-accent"
              : "border-line-strong text-ink-muted hover:border-accent hover:text-accent",
          )}
        >
          {label}
        </button>,
      );
    } else {
      nodes.push(match[0]);
    }

    cursor = match.index + match[0].length;
  }

  if (cursor < line.length) nodes.push(line.slice(cursor));
  return nodes;
}
