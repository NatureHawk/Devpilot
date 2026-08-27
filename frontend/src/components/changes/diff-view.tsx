"use client";

import { cn } from "@/lib/cn";

/**
 * Renders a unified diff as a review surface.
 *
 * Line numbers on both sides, additions and removals distinguished by colour
 * and by a gutter marker so the diff is still readable without colour. Diff
 * text comes from the backend and is rendered as data — never as markup.
 */
export function DiffView({ diff }: { diff: string }) {
  const files = splitByFile(diff);

  if (files.length === 0) {
    return (
      <p className="text-ink-muted px-4 py-6 text-sm">This proposal contains no changes.</p>
    );
  }

  return (
    <div className="divide-line divide-y">
      {files.map((file) => (
        <FileDiff key={file.path} file={file} />
      ))}
    </div>
  );
}

type DiffFile = { path: string; lines: string[] };

/** Splits a multi-file unified diff on its `--- a/…` headers. */
function splitByFile(diff: string): DiffFile[] {
  const files: DiffFile[] = [];
  let current: DiffFile | null = null;

  for (const line of diff.split("\n")) {
    if (line.startsWith("--- a/")) {
      if (current) files.push(current);
      current = { path: line.slice("--- a/".length), lines: [] };
      continue;
    }
    // The +++ header repeats the path; the heading already shows it.
    if (line.startsWith("+++ b/")) continue;
    if (current) current.lines.push(line);
  }

  if (current) files.push(current);
  return files;
}

type DiffRow =
  | { kind: "hunk"; text: string }
  | { kind: "add" | "remove" | "context"; text: string; left: number | null; right: number | null };

/**
 * Assigns line numbers to each diff row.
 *
 * A pure pass before render: counters advance while walking the hunks, which
 * cannot be done during rendering without mutating across elements.
 */
function numberRows(lines: string[]): DiffRow[] {
  const rows: DiffRow[] = [];
  let oldLine = 0;
  let newLine = 0;

  for (const text of lines) {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(text);
    if (hunk) {
      // A hunk header restates where both sides resume.
      oldLine = Number(hunk[1]);
      newLine = Number(hunk[2]);
      rows.push({ kind: "hunk", text });
      continue;
    }

    if (text.startsWith("+")) {
      rows.push({ kind: "add", text, left: null, right: newLine++ });
    } else if (text.startsWith("-")) {
      rows.push({ kind: "remove", text, left: oldLine++, right: null });
    } else {
      rows.push({ kind: "context", text, left: oldLine++, right: newLine++ });
    }
  }

  return rows;
}

function FileDiff({ file }: { file: DiffFile }) {
  const added = file.lines.filter((line) => line.startsWith("+")).length;
  const removed = file.lines.filter((line) => line.startsWith("-")).length;
  const rows = numberRows(file.lines);

  return (
    <section>
      <header className="border-line bg-surface flex items-center gap-3 border-b px-4 py-2.5">
        <h4 className="text-ink min-w-0 flex-1 truncate font-mono text-xs">{file.path}</h4>
        <span className="text-2xs shrink-0 font-mono">
          <span className="text-success">+{added}</span>{" "}
          <span className="text-danger">−{removed}</span>
        </span>
      </header>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse font-mono text-xs">
          <tbody>
            {rows.map((row, index) =>
              row.kind === "hunk" ? (
                <tr key={index} className="bg-surface-hover">
                  <td colSpan={3} className="text-ink-faint px-4 py-1 select-none">
                    {row.text}
                  </td>
                </tr>
              ) : (
                <tr
                  key={index}
                  className={cn(row.kind === "add" && "bg-success/8", row.kind === "remove" && "bg-danger/8")}
                >
                  <td className="text-ink-faint border-line/50 w-12 border-r px-2 py-0.5 text-right align-top select-none">
                    {row.left ?? ""}
                  </td>
                  <td className="text-ink-faint border-line/50 w-12 border-r px-2 py-0.5 text-right align-top select-none">
                    {row.right ?? ""}
                  </td>
                  <td
                    className={cn(
                      "px-3 py-0.5 whitespace-pre",
                      row.kind === "add" && "text-success",
                      row.kind === "remove" && "text-danger",
                      row.kind === "context" && "text-ink-muted",
                    )}
                  >
                    {row.text || " "}
                  </td>
                </tr>
              ),
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
