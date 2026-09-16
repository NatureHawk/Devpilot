"use client";

import { ChevronRight } from "lucide-react";
import { useState } from "react";

import { ChangeCard } from "@/components/changes/change-card";
import { ButtonLink } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import type { ChangeStatus, ProposedChange } from "@/lib/api";
import { repositoryPath } from "@/lib/navigation";
import type { NextAction } from "@/lib/workflow";

const GROUPS: { id: string; title: string; description: string; statuses: ChangeStatus[] }[] = [
  {
    id: "needs-review",
    title: "Needs your review",
    description: "Read each diff, then approve or reject it.",
    statuses: ["proposed"],
  },
  {
    id: "ready-to-ship",
    title: "Approved — ready to ship",
    description: "Create a branch and pull request for each approved change.",
    statuses: ["approved", "executing", "committed"],
  },
  {
    id: "shipped",
    title: "Shipped",
    description: "Pull requests DevPilot opened on GitHub.",
    statuses: ["pr_created"],
  },
];

const CLOSED: ChangeStatus[] = ["rejected", "stale", "failed"];

/**
 * Review: proposals grouped by what they need from you, most urgent first.
 * Closed proposals stay available but collapsed.
 */
export function ReviewWorkspace({
  owner,
  name,
  initialChanges,
  next,
}: {
  owner: string;
  name: string;
  initialChanges: ProposedChange[];
  /** The workflow's next step; decides what an empty review points to. */
  next?: NextAction | undefined;
}) {
  const [changes, setChanges] = useState(initialChanges);
  const update = (next: ProposedChange) =>
    setChanges((current) => current.map((change) => (change.id === next.id ? next : change)));

  if (changes.length === 0) {
    // Nothing has been asked yet: understanding comes before investigating.
    if (next && (next.stage === "ask" || next.stage === "repository")) {
      return (
        <EmptyState
          title="No proposals to review yet"
          description="Start by understanding the code. Ask a question and DevPilot will find the relevant area of the codebase — from there, investigate a change to review here."
        >
          <ButtonLink
            href={repositoryPath(owner, name, next.segment)}
            variant="primary"
            size="lg"
            forward
          >
            {next.label}
          </ButtonLink>
        </EmptyState>
      );
    }
    return (
      <EmptyState
        title="No proposals to review yet"
        description="Proposals come from investigations. Describe a change, DevPilot proposes a diff, and you review it here before anything reaches GitHub."
      >
        <ButtonLink
          href={repositoryPath(owner, name, "changes")}
          variant="primary"
          size="lg"
          forward
        >
          Investigate a change
        </ButtonLink>
      </EmptyState>
    );
  }

  const closed = changes.filter((change) => CLOSED.includes(change.status));

  return (
    <div className="space-y-10">
      {GROUPS.map((group) => {
        const items = changes.filter((change) => group.statuses.includes(change.status));
        if (items.length === 0) return null;
        return (
          <section key={group.id} aria-labelledby={group.id}>
            <h2 id={group.id} className="text-ink text-base font-semibold">
              {group.title} <span className="text-ink-faint font-normal">· {items.length}</span>
            </h2>
            <p className="text-ink-muted mt-0.5 text-sm">{group.description}</p>
            <div className="mt-4 space-y-4">
              {items.map((change) => (
                <ChangeCard
                  key={change.id}
                  change={change}
                  owner={owner}
                  name={name}
                  onChange={update}
                />
              ))}
            </div>
          </section>
        );
      })}

      {closed.length > 0 ? (
        <details className="group">
          <summary className="text-ink-muted hover:text-ink flex w-fit cursor-pointer list-none items-center gap-1.5 text-sm transition-colors">
            <ChevronRight
              aria-hidden="true"
              className="size-3.5 transition-transform group-open:rotate-90"
              strokeWidth={2}
            />
            Closed · {closed.length}
            <span className="text-ink-faint text-xs">rejected, out of date or failed</span>
          </summary>
          <div className="mt-4 space-y-4">
            {closed.map((change) => (
              <ChangeCard
                key={change.id}
                change={change}
                owner={owner}
                name={name}
                onChange={update}
              />
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}
