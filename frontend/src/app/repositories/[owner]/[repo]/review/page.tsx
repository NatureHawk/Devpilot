import type { Metadata } from "next";

import { ReviewWorkspace } from "@/components/changes/review-workspace";
import { StageHeading } from "@/components/layout/stage-heading";
import { NotConnectedState } from "@/components/repository/repository-states";
import { ApiErrorState } from "@/components/ui/error-state";
import {
  decodeParams,
  loadChanges,
  loadRepository,
  loadWorkflowContext,
  type RepositoryParams,
} from "@/lib/repository-context";

export const metadata: Metadata = { title: "Review" };

export default async function ReviewPage({ params }: { params: Promise<RepositoryParams> }) {
  const { owner, repo } = decodeParams(await params);
  const result = await loadRepository(owner, repo);

  if (!result.ok) {
    return (
      <Body>
        {result.error.code === "not_found" ? (
          <NotConnectedState owner={owner} repo={repo} />
        ) : (
          <ApiErrorState error={result.error} />
        )}
      </Body>
    );
  }

  const [changes, context] = await Promise.all([
    loadChanges(result.data.id),
    loadWorkflowContext(result.data),
  ]);

  return (
    <Body>
      <StageHeading
        step="review"
        title="Review changes"
        description="Every proposed change is a diff you decide on. Approving records your decision; creating the pull request is the next step."
      />
      <div className="mt-8">
        {changes.ok ? (
          <ReviewWorkspace
            owner={owner}
            name={repo}
            initialChanges={changes.data.items}
            next={context.workflow.next}
          />
        ) : (
          <ApiErrorState error={changes.error} />
        )}
      </div>
    </Body>
  );
}

function Body({ children }: { children: React.ReactNode }) {
  return (
    <main className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-6 py-8">{children}</div>
    </main>
  );
}
