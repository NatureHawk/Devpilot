"use client";

import { ExternalLink, GitPullRequest, Loader2 } from "lucide-react";
import { useState } from "react";

import { executeChangeAction } from "@/app/actions";
import { Button, buttonStyles } from "@/components/ui/button";
import type { ProposedChange } from "@/lib/api";

/** Creates the branch and pull request for one approved change, in place. */
export function ShipButton({
  change,
  owner,
  name,
}: {
  change: ProposedChange;
  owner: string;
  name: string;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [shipped, setShipped] = useState<ProposedChange | null>(null);

  if (shipped?.pr_url) {
    return (
      <a href={shipped.pr_url} target="_blank" rel="noreferrer" className={buttonStyles("primary")}>
        View pull request #{shipped.pr_number}
        <ExternalLink aria-hidden="true" className="size-3.5" strokeWidth={2} />
        <span className="sr-only">(opens GitHub in a new tab)</span>
      </a>
    );
  }

  const retry = change.status === "committed";

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      <Button
        variant="primary"
        disabled={pending || change.status === "executing"}
        onClick={async () => {
          setPending(true);
          setError(null);
          const result = await executeChangeAction(change.id, owner, name);
          setPending(false);
          if (result.ok) setShipped(result.data);
          else setError(result.error.message);
        }}
      >
        {pending || change.status === "executing" ? (
          <Loader2 aria-hidden="true" className="size-3.5 animate-spin" strokeWidth={2} />
        ) : (
          <GitPullRequest aria-hidden="true" className="size-3.5" strokeWidth={2} />
        )}
        {change.status === "executing"
          ? "Creating pull request…"
          : pending
            ? "Creating branch & PR…"
            : retry
              ? "Retry pull request"
              : "Create branch & PR"}
      </Button>
      {error ? (
        <span role="alert" className="text-danger text-sm">
          {error}
        </span>
      ) : null}
    </div>
  );
}
