import { NotConnectedState } from "@/components/repository/repository-states";
import { EmptyState } from "@/components/ui/empty-state";
import { ApiErrorState } from "@/components/ui/error-state";
import { Panel } from "@/components/ui/panel";
import { decodeParams, loadRepository, type RepositoryParams } from "@/lib/repository-context";

export default async function PullRequestsPage({ params }: { params: Promise<RepositoryParams> }) {
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
            <EmptyState
              title="No pull requests from DevPilot"
              description="Approved changes can eventually be committed to a branch and opened as a GitHub pull request. Pull requests opened this way are listed here alongside their review status."
            />
          </Panel>
        )}
      </div>
    </div>
  );
}
