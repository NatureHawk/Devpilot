/**
 * Right-hand source panel.
 *
 * Empty by design: it fills in only when a question has actually been answered
 * and there are real retrieved sources to cite. The legend below describes what
 * each entry will carry, so the panel explains itself before it has content.
 */
const CONTEXT_FIELDS = [
  { label: "File path", detail: "Where the excerpt lives in the repository." },
  { label: "Language", detail: "How the excerpt is parsed and highlighted." },
  { label: "Line range", detail: "The exact lines the answer drew on." },
  { label: "Relevance", detail: "How strongly the excerpt matched the question." },
] as const;

export function ContextPanel() {
  return (
    <aside
      aria-label="Repository context"
      className="border-line bg-surface hidden w-[300px] shrink-0 flex-col overflow-y-auto border-l xl:flex"
    >
      <header className="border-line flex h-10 shrink-0 items-center border-b px-4">
        <h2 className="text-ink text-xs font-medium">Repository context</h2>
      </header>

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
    </aside>
  );
}
