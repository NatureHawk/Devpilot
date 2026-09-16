"use client";

import { X } from "lucide-react";
import { useEffect, useRef } from "react";

import { basename } from "@/components/ask/answer";
import { cn } from "@/lib/cn";
import type { AskSource } from "@/lib/ask-stream";

/**
 * The code behind a citation, with its real line numbers.
 *
 * `side` is a panel beside the conversation on wide screens; `inline` sits
 * under the evidence on narrow ones. Only the visible variant takes focus, and
 * Escape closes either.
 */
export function SourceViewer({
  source,
  onClose,
  variant,
}: {
  source: AskSource;
  onClose: () => void;
  variant: "side" | "inline";
}) {
  const panelRef = useRef<HTMLElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const headingId = `source-${variant}-${source.label}`;

  useEffect(() => {
    // Both variants are in the DOM; only move focus into the one on screen.
    if (panelRef.current && panelRef.current.offsetParent !== null) {
      headingRef.current?.focus();
    }
  }, [source.label, source.chunk_id]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const lines = source.content ? source.content.replace(/\n$/, "").split("\n") : [];

  return (
    <aside
      ref={panelRef}
      aria-labelledby={headingId}
      className={cn(
        "bg-surface flex min-h-0 flex-col",
        variant === "side"
          ? "border-line animate-panel hidden w-[min(40vw,480px)] shrink-0 border-l lg:flex"
          : "border-line animate-in mt-3 max-h-[28rem] rounded-lg border lg:hidden",
      )}
    >
      <header className="border-line flex items-start gap-3 border-b px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-2xs border-accent/40 text-accent rounded border px-1 font-mono">
              {source.label}
            </span>
            <h2
              id={headingId}
              ref={headingRef}
              tabIndex={-1}
              className="text-ink truncate text-sm font-semibold focus:outline-none"
            >
              {source.symbol ?? basename(source.file_path)}
            </h2>
          </div>
          <p className="text-ink-faint mt-1 truncate font-mono text-xs" title={source.file_path}>
            {source.file_path}
          </p>
          <p className="text-ink-faint text-xs">
            Lines {source.start_line}–{source.end_line}
            {source.language ? ` · ${source.language}` : ""}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close source"
          className="text-ink-faint hover:bg-surface-hover hover:text-ink -mr-1 rounded-md p-1 transition-colors"
        >
          <X aria-hidden="true" className="size-4" strokeWidth={2} />
        </button>
      </header>

      {lines.length > 0 ? (
        <div className="min-h-0 flex-1 overflow-auto">
          {/* Repository source is untrusted text: React escapes it and it is
              rendered as data, never as markup. */}
          <table className="w-full border-collapse font-mono text-xs leading-5">
            <tbody>
              {lines.map((line, index) => (
                <tr key={index}>
                  <td className="text-ink-faint w-12 px-3 text-right align-top select-none">
                    {source.start_line + index}
                  </td>
                  <td className="text-ink-muted pr-4 whitespace-pre">{line || " "}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-ink-muted px-4 py-4 text-sm">
          Code isn&apos;t stored with past answers. Ask the question again to see the current code
          for this source.
        </p>
      )}
    </aside>
  );
}
