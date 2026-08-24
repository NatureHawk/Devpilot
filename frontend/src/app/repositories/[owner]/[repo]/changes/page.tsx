import { Check, Eye, GitCommitHorizontal } from "lucide-react";

import { NotConnectedState } from "@/components/repository/repository-states";
import { ApiErrorState } from "@/components/ui/error-state";
import { Panel, PanelHeader } from "@/components/ui/panel";
import { decodeParams, loadRepository, type RepositoryParams } from "@/lib/repository-context";

const REVIEW_STEPS = [
  {
    icon: Eye,
    title: "Read the diff",
    detail: "Every proposed edit is shown as a file-by-file diff before it exists anywhere else.",
  },
  {
    icon: Check,
    title: "Approve or reject",
    detail: "Nothing is applied without an explicit decision from you on each change.",
  },
  {
    icon: GitCommitHorizontal,
    title: "Commit to a branch",
    detail: "Approved changes are committed to a branch — never straight to the default one.",
  },
] as const;

export default async function ChangesPage({ params }: { params: Promise<RepositoryParams> }) {
  const { owner, repo } = decodeParams(await params);
  const result = await loadRepository(owner, repo);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-6 py-8">
        {!result.ok ? (
          result.error.code === "not_found" ? (
            <NotConnectedState owner={owner} repo={repo} />
          ) : (
            <ApiErrorState error={result.error} />
          )
        ) : (
          <Panel>
            <PanelHeader
              title="No proposed changes"
              description="AI-generated modifications will appear here for review before anything is written back to the repository."
            />

            {/* The review contract, stated up front: this is the step that keeps
                a human between a generated edit and the repository. */}
            <ul className="divide-line grid grid-cols-1 divide-y md:grid-cols-3 md:divide-x md:divide-y-0">
              {REVIEW_STEPS.map(({ icon: Icon, title, detail }) => (
                <li key={title} className="px-5 py-5">
                  <Icon aria-hidden="true" className="text-ink-faint size-4" strokeWidth={1.75} />
                  <h3 className="text-ink mt-3 text-sm font-medium">{title}</h3>
                  <p className="text-ink-muted mt-1 text-xs leading-relaxed">{detail}</p>
                </li>
              ))}
            </ul>

            <div className="border-line border-t px-5 py-3">
              <p className="text-2xs text-ink-faint">
                DevPilot never writes to a repository without an approved review.
              </p>
            </div>
          </Panel>
        )}
      </div>
    </div>
  );
}
