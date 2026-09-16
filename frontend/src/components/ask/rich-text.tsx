import type { ReactNode } from "react";

/**
 * A deliberately small markdown renderer for answers.
 *
 * Models write headings, lists, emphasis and code; showing `###` and `**`
 * literally makes an answer hard to scan. Everything is built as React
 * elements from parsed text — there is no HTML parsing and no
 * dangerouslySetInnerHTML — so repository-derived content can never become
 * markup. Unsupported syntax simply stays text.
 */

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "code"; language: string; text: string }
  | { kind: "quote"; text: string };

export type CitationRenderer = (labels: string[], key: string) => ReactNode;

export function parseBlocks(markdown: string): Block[] {
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  // Held on an object so each flush is visible to the loop's narrowing.
  const open: { paragraph: string[]; list: { ordered: boolean; items: string[] } | null } = {
    paragraph: [],
    list: null,
  };

  const flushParagraph = () => {
    if (open.paragraph.length > 0) {
      blocks.push({ kind: "paragraph", text: open.paragraph.join(" ") });
      open.paragraph = [];
    }
  };
  const flushList = () => {
    if (open.list) {
      blocks.push({ kind: "list", ordered: open.list.ordered, items: open.list.items });
      open.list = null;
    }
  };

  for (let index = 0; index < lines.length; index++) {
    const line = lines[index] ?? "";

    const fence = /^\s*```\s*([\w+-]*)\s*$/.exec(line);
    if (fence) {
      flushParagraph();
      flushList();
      const body: string[] = [];
      index++;
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index] ?? "")) {
        body.push(lines[index] ?? "");
        index++;
      }
      blocks.push({ kind: "code", language: fence[1] ?? "", text: body.join("\n") });
      continue;
    }

    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }

    const heading = /^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
    if (heading) {
      flushParagraph();
      flushList();
      blocks.push({ kind: "heading", level: heading[1]?.length ?? 3, text: heading[2] ?? "" });
      continue;
    }

    const bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
    const numbered = bullet ? null : /^\s*\d+[.)]\s+(.*)$/.exec(line);
    const item = bullet ?? numbered;
    if (item) {
      flushParagraph();
      const ordered = numbered !== null;
      if (!open.list || open.list.ordered !== ordered) {
        flushList();
        open.list = { ordered, items: [] };
      }
      open.list.items.push((item[1] ?? "").trim());
      continue;
    }

    const quote = /^\s*>\s?(.*)$/.exec(line);
    if (quote) {
      flushParagraph();
      flushList();
      blocks.push({ kind: "quote", text: quote[1] ?? "" });
      continue;
    }

    // An indented line directly under a list item continues that item.
    if (open.list && /^\s{2,}\S/.test(line)) {
      const last = open.list.items.length - 1;
      open.list.items[last] = `${open.list.items[last] ?? ""} ${line.trim()}`;
      continue;
    }

    flushList();
    open.paragraph.push(line.trim());
  }

  flushParagraph();
  flushList();
  return blocks;
}

// Citations, inline code, bold, and italics that are not part of identifiers
// (so `get_db_path` is never read as emphasis).
const INLINE =
  /(\[S\d+(?:\s*,\s*S\d+)*\])|(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(__[^_\n]+__)|((?<![\w*])\*[^*\s][^*\n]*\*(?![\w*]))|((?<![\w_])_[^_\s][^_\n]*_(?![\w_]))/g;

export function renderInline(
  text: string,
  renderCitation: CitationRenderer,
  keyPrefix = "i",
): ReactNode[] {
  const nodes: ReactNode[] = [];
  let cursor = 0;
  let position = 0;

  for (const match of text.matchAll(INLINE)) {
    const token = match[0];
    const start = match.index ?? 0;
    if (start > cursor) nodes.push(text.slice(cursor, start));
    const key = `${keyPrefix}-${position++}`;

    if (token.startsWith("[S")) {
      nodes.push(renderCitation(token.slice(1, -1).split(/\s*,\s*/), key));
    } else if (token.startsWith("`")) {
      nodes.push(
        <code
          key={key}
          className="bg-surface-hover text-ink rounded px-1 py-px font-mono text-[0.85em]"
        >
          {token.slice(1, -1)}
        </code>,
      );
    } else if (token.startsWith("**") || token.startsWith("__")) {
      nodes.push(
        <strong key={key} className="text-ink font-semibold">
          {renderInline(token.slice(2, -2), renderCitation, key)}
        </strong>,
      );
    } else {
      nodes.push(
        <em key={key} className="italic">
          {renderInline(token.slice(1, -1), renderCitation, key)}
        </em>,
      );
    }

    cursor = start + token.length;
  }

  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

export function RichText({
  text,
  renderCitation,
}: {
  text: string;
  renderCitation: CitationRenderer;
}) {
  const blocks = parseBlocks(text);

  return (
    <div className="space-y-3">
      {blocks.map((block, index) => {
        const key = `b-${index}`;
        switch (block.kind) {
          case "heading":
            return block.level <= 2 ? (
              <h3 key={key} className="text-ink pt-2 text-base font-semibold">
                {renderInline(block.text, renderCitation, key)}
              </h3>
            ) : (
              <h4 key={key} className="text-ink pt-1 text-sm font-semibold">
                {renderInline(block.text, renderCitation, key)}
              </h4>
            );
          case "paragraph":
            return <p key={key}>{renderInline(block.text, renderCitation, key)}</p>;
          case "list": {
            const items = block.items.map((item, itemIndex) => (
              <li key={`${key}-${itemIndex}`} className="pl-1">
                {renderInline(item, renderCitation, `${key}-${itemIndex}`)}
              </li>
            ));
            return block.ordered ? (
              <ol key={key} className="marker:text-ink-faint list-decimal space-y-1.5 pl-5">
                {items}
              </ol>
            ) : (
              <ul key={key} className="marker:text-ink-faint list-disc space-y-1.5 pl-5">
                {items}
              </ul>
            );
          }
          case "code":
            return (
              <pre
                key={key}
                className="border-line bg-canvas text-ink-muted overflow-x-auto rounded-md border px-3 py-2.5 font-mono text-xs leading-relaxed"
              >
                {block.text}
              </pre>
            );
          case "quote":
            return (
              <blockquote key={key} className="border-line-strong text-ink-muted border-l-2 pl-3">
                {renderInline(block.text, renderCitation, key)}
              </blockquote>
            );
        }
      })}
    </div>
  );
}

const MEANING_HEADING =
  /^\s{0,3}(?:#{1,6}\s*|\*\*|__)?\s*what this means\s*(?:\*\*|__)?\s*:?\s*$/im;

/**
 * Separates a closing "What this means" section, when the answer has one, so it
 * can be presented as the conclusion rather than as more answer text.
 */
export function splitAnswer(text: string): { answer: string; meaning: string | null } {
  const match = MEANING_HEADING.exec(text);
  if (!match) return { answer: text, meaning: null };
  const meaning = text.slice(match.index + match[0].length).trim();
  return { answer: text.slice(0, match.index).trimEnd(), meaning: meaning || null };
}
