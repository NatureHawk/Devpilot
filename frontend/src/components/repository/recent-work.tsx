import { ArrowRight, MessageSquareText, ScanSearch } from "lucide-react";
import Link from "next/link";

import type { ChangeStatus, Conversation, ProposedChange } from "@/lib/api";
import { repositoryPath } from "@/lib/navigation";

export const CHANGE_STATUS_LABEL: Record<ChangeStatus, string> = {
  proposed: "Needs review",
  approved: "Ready to ship",
  rejected: "Rejected",
  stale: "Out of date",
  failed: "Failed",
  executing: "Opening pull request…",
  committed: "Pull request failed — retry",
  pr_created: "Pull request open",
};

/** Real threads and proposals for this repository, newest first. Hidden when there are none. */
export function RecentWork({
  owner,
  repo,
  conversations,
  changes,
}: {
  owner: string;
  repo: string;
  conversations: Conversation[];
  changes: ProposedChange[];
}) {
  if (conversations.length === 0 && changes.length === 0) return null;

  return (
    <section aria-labelledby="recent-work" className="grid gap-8 sm:grid-cols-2">
      <h2 id="recent-work" className="sr-only">
        Recent work
      </h2>

      {conversations.length > 0 ? (
        <div>
          <h3 className="text-ink-faint text-xs font-medium tracking-wide uppercase">
            Recent questions
          </h3>
          <ul className="mt-2 space-y-0.5">
            {conversations.slice(0, 5).map((conversation) => (
              <li key={conversation.id}>
                <Link
                  href={`${repositoryPath(owner, repo, "ask")}?conversation=${conversation.id}`}
                  className="group hover:bg-surface-hover -mx-2 flex items-center gap-2 rounded-md px-2 py-1.5 transition-colors"
                >
                  <MessageSquareText
                    aria-hidden="true"
                    className="text-ink-faint size-3.5 shrink-0"
                    strokeWidth={1.75}
                  />
                  <span className="text-ink-muted group-hover:text-ink min-w-0 flex-1 truncate text-sm">
                    {conversation.title ?? "Untitled conversation"}
                  </span>
                  <ArrowRight
                    aria-hidden="true"
                    className="text-ink-faint size-3.5 shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
                    strokeWidth={2}
                  />
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {changes.length > 0 ? (
        <div>
          <h3 className="text-ink-faint text-xs font-medium tracking-wide uppercase">
            Investigations
          </h3>
          <ul className="mt-2 space-y-0.5">
            {changes.slice(0, 5).map((change) => (
              <li key={change.id}>
                <Link
                  href={`${repositoryPath(owner, repo, "review")}#change-${change.id}`}
                  className="group hover:bg-surface-hover -mx-2 flex items-center gap-2 rounded-md px-2 py-1.5 transition-colors"
                >
                  <ScanSearch
                    aria-hidden="true"
                    className="text-ink-faint size-3.5 shrink-0"
                    strokeWidth={1.75}
                  />
                  <span className="text-ink-muted group-hover:text-ink min-w-0 flex-1 truncate text-sm">
                    {change.request}
                  </span>
                  <span className="text-2xs text-ink-faint shrink-0">
                    {CHANGE_STATUS_LABEL[change.status]}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
