import { ChangesWorkspace } from "@/components/changes/changes-workspace";
import { NotConnectedState } from "@/components/repository/repository-states";
import { ApiErrorState } from "@/components/ui/error-state";
import { getIntegrations, listChanges } from "@/lib/api";
import { decodeParams, loadRepository, type RepositoryParams } from "@/lib/repository-context";

/** Names the first thing standing between the user and a proposal. */
function blockingReason(indexed: boolean, llmConfigured: boolean, embeddings: boolean) {
  if (!indexed) return "Index this repository before requesting changes.";
  if (!embeddings) return "No embedding provider is configured, so the code cannot be searched.";
  if (!llmConfigured) return "No language model is configured for this deployment.";
  return null;
}

export default async function ChangesPage({ params }: { params: Promise<RepositoryParams> }) {
  const { owner, repo } = decodeParams(await params);
  const result = await loadRepository(owner, repo);

  if (!result.ok) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-5xl px-6 py-8">
          {result.error.code === "not_found" ? (
            <NotConnectedState owner={owner} repo={repo} />
          ) : (
            <ApiErrorState error={result.error} />
          )}
        </div>
      </div>
    );
  }

  const repository = result.data;
  const [changesResult, integrationsResult] = await Promise.all([
    listChanges(repository.id),
    getIntegrations(),
  ]);

  const integrations = integrationsResult.ok ? integrationsResult.data.integrations : [];
  const configured = (name: string) =>
    integrations.find((integration) => integration.name === name)?.configured ?? false;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-6 py-8">
        {!changesResult.ok ? <ApiErrorState error={changesResult.error} className="mb-6" /> : null}

        <ChangesWorkspace
          repositoryId={repository.id}
          owner={owner}
          name={repo}
          initialChanges={changesResult.ok ? changesResult.data.items : []}
          disabledReason={blockingReason(
            repository.indexing_status === "indexed",
            configured("ai_provider"),
            configured("embeddings"),
          )}
        />
      </div>
    </div>
  );
}
