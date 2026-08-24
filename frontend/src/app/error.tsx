"use client";

import { useEffect } from "react";

import { PageBody, PageHeader, PageTitle } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";

/**
 * Route-level boundary: any render or data error below this point produces a
 * usable screen with a retry, never a blank document.
 */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Unhandled route error", error);
  }, [error]);

  return (
    <>
      <PageHeader>
        <PageTitle>Error</PageTitle>
      </PageHeader>
      <PageBody>
        <EmptyState
          title="This page failed to load"
          description="The error was logged. Retrying is usually enough; if it persists, check that the DevPilot API is running."
        >
          <div className="flex items-center gap-2">
            <Button variant="primary" onClick={reset}>
              Try again
            </Button>
            {error.digest ? (
              <span className="text-2xs text-ink-faint font-mono">{error.digest}</span>
            ) : null}
          </div>
        </EmptyState>
      </PageBody>
    </>
  );
}
