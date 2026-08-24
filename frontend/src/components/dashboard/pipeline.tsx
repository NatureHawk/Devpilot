const STAGES = [
  { label: "Connect", detail: "Point DevPilot at a repository you work in." },
  { label: "Index", detail: "Its structure is parsed and prepared for search." },
  { label: "Ask", detail: "Questions are answered from the code itself." },
  { label: "Review", detail: "Proposed edits arrive as a diff you approve." },
  { label: "Ship", detail: "Approved work opens as a pull request." },
] as const;

/**
 * The product in five steps. Structural rather than decorative: it states the
 * path a repository takes through DevPilot without illustrating features that
 * do not exist yet.
 */
export function Pipeline() {
  return (
    <ol className="divide-line grid grid-cols-1 divide-y lg:grid-cols-5 lg:divide-x lg:divide-y-0">
      {STAGES.map((stage, index) => (
        <li key={stage.label} className="px-4 py-4">
          <span className="text-2xs text-ink-faint font-mono">
            {String(index + 1).padStart(2, "0")}
          </span>
          <h3 className="text-ink mt-1.5 text-sm font-medium">{stage.label}</h3>
          <p className="text-ink-muted mt-1 text-xs leading-relaxed">{stage.detail}</p>
        </li>
      ))}
    </ol>
  );
}
