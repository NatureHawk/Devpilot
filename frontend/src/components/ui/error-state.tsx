import { AlertTriangle } from "lucide-react";

import { cn } from "@/lib/cn";
import type { ApiError } from "@/lib/api";

/** Failure modes a developer can act on get their own copy. */
const KNOWN: Partial<Record<ApiError["code"], { title: string; detail: string }>> = {
  // During local development this almost always means the API is not running.
  unreachable: {
    title: "Cannot reach the DevPilot API",
    detail:
      "The API did not respond. Confirm the backend is running and that BACKEND_URL points at it.",
  },
  // The API answered, so it is up — its database is not.
  service_unavailable: {
    title: "The database is unavailable",
    detail:
      "The API is running but cannot reach PostgreSQL. Confirm the database is started and that DATABASE_URL is correct, then run migrations.",
  },
};

/**
 * Renders a failed API call, naming the cause whenever the code identifies one.
 */
export function ApiErrorState({ error, className }: { error: ApiError; className?: string }) {
  const known = KNOWN[error.code];

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
        <p className="text-ink text-sm font-medium">{known?.title ?? "Something went wrong"}</p>
        <p className="text-ink-muted mt-1 text-sm leading-relaxed">
          {known?.detail ?? error.message}
        </p>
        {error.status ? (
          <p className="text-2xs text-ink-faint mt-2 font-mono">
            {error.code} · {error.status}
          </p>
        ) : null}
      </div>
    </div>
  );
}
