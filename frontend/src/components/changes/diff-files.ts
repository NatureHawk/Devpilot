export type DiffFileSummary = { path: string; added: number; removed: number };

/**
 * Files touched by a unified diff, with line counts.
 *
 * Follows the same file boundaries DiffView renders (`--- a/…` headers), so the
 * summary and the diff below it always agree.
 */
export function summarizeDiff(diff: string): DiffFileSummary[] {
  const files: DiffFileSummary[] = [];
  let current: DiffFileSummary | undefined;

  for (const line of diff.split("\n")) {
    if (line.startsWith("--- a/")) {
      current = { path: line.slice("--- a/".length), added: 0, removed: 0 };
      files.push(current);
      continue;
    }
    if (!current || line.startsWith("+++ ")) continue;
    if (line.startsWith("+")) current.added += 1;
    else if (line.startsWith("-")) current.removed += 1;
  }

  return files;
}
