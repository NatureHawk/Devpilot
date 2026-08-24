import { cn } from "@/lib/cn";

/**
 * DevPilot mark: a caret, the character a developer already reads as "prompt".
 * Drawn rather than imported so it inherits currentColor in both themes.
 */
export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("flex items-center gap-2", className)}>
      <svg
        viewBox="0 0 16 16"
        aria-hidden="true"
        className="text-accent size-4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M4 4.5 8 8l-4 3.5" />
        <path d="M9.5 11.5H13" />
      </svg>
      <span className="text-ink sr-only text-sm font-semibold tracking-tight lg:not-sr-only">
        DevPilot
      </span>
    </span>
  );
}
