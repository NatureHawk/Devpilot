const STEPS = [
  { label: "Connect", detail: "Choose a GitHub repository you work in." },
  { label: "Index", detail: "DevPilot parses the code so it can be searched." },
  { label: "Understand", detail: "Ask questions; answers cite the exact files and lines." },
  { label: "Investigate", detail: "Describe a change; DevPilot proposes a diff." },
  { label: "Review", detail: "Read the diff and approve or reject it." },
  { label: "Ship", detail: "Open a pull request with the exact diff you approved." },
] as const;

/** The journey DevPilot walks you through, before you have started it. */
export function WorkflowOverview({ className }: { className?: string }) {
  return (
    <section aria-labelledby="how-it-works" className={className}>
      <h2
        id="how-it-works"
        className="text-ink-faint text-xs font-semibold tracking-[0.08em] uppercase"
      >
        The journey
      </h2>
      <ol className="mt-3">
        {STEPS.map((step, index) => (
          <li key={step.label} className="relative flex gap-3 pb-4 last:pb-0">
            {index < STEPS.length - 1 ? (
              <span
                aria-hidden="true"
                className="bg-line-strong absolute top-6 bottom-0 left-2.5 w-px"
              />
            ) : null}
            <span
              aria-hidden="true"
              className="border-line-strong bg-canvas text-2xs text-ink-muted relative flex size-5 shrink-0 items-center justify-center rounded-full border font-mono"
            >
              {index + 1}
            </span>
            <p className="text-sm">
              <span className="text-ink font-medium">{step.label}</span>
              <span className="text-ink-muted"> — {step.detail}</span>
            </p>
          </li>
        ))}
      </ol>
    </section>
  );
}
