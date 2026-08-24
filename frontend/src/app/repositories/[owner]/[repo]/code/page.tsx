import { NotConnectedState } from "@/components/repository/repository-states";
import { EmptyState } from "@/components/ui/empty-state";
import { ApiErrorState } from "@/components/ui/error-state";
import { Panel } from "@/components/ui/panel";
import { decodeParams, loadRepository, type RepositoryParams } from "@/lib/repository-context";

export default async function RepositoryCodePage({
  params,
}: {
  params: Promise<RepositoryParams>;
}) {
  const { owner, repo } = decodeParams(await params);
  const result = await loadRepository(owner, repo);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-6 py-8">
        {result.ok ? (
          <Panel>
            <EmptyState
              title="No files to browse yet"
              description="Once this repository is indexed, its file tree is browsable here, with the same code DevPilot reads when it answers a question."
            />
          </Panel>
        ) : result.error.code === "not_found" ? (
          <NotConnectedState owner={owner} repo={repo} />
        ) : (
          <ApiErrorState error={result.error} />
        )}
      </div>
    </div>
  );
}
