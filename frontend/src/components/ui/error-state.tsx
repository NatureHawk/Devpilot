import { AlertTriangle } from "lucide-react";

import { cn } from "@/lib/cn";
import type { ApiError } from "@/lib/api";

/**
 * Renders a failed API call. "Unreachable" gets its own copy because during
 * local development it almost always means the API process is not running, and
 * telling the developer that is more useful than a generic failure notice.
 */
export function ApiErrorState({ error, className }: { error: ApiError; className?: string }) {
  const unreachable = error.code === "unreachable";

  return (
    <div
      role="alert"
      className={cn(
        "border-line bg-surface flex items-start gap-3 rounded-lg border px-4 py-3.5",
        className,
      )}
    >
      <AlertTriangle
        aria-hidden="true"
        className="text-warning mt-0.5 size-4 shrink-0"
        strokeWidth={1.75}
      />
      <div className="min-w-0">
        <p className="text-ink text-sm font-medium">
          {unreachable ? "Cannot reach the DevPilot API" : "Something went wrong"}
        </p>
        <p className="text-ink-muted mt-1 text-sm leading-relaxed">
          {unreachable
            ? "The API did not respond. Confirm the backend is running and that BACKEND_URL points at it."
            : error.message}
        </p>
        {!unreachable ? (
          <p className="text-2xs text-ink-faint mt-2 font-mono">
            {error.code}
            {error.status ? ` · ${error.status}` : null}
          </p>
        ) : null}
      </div>
    </div>
  );
}
