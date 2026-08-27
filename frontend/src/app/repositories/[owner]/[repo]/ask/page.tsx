import { AskWorkspace } from "@/components/ask/ask-workspace";
import { getIntegrations, type ApiResult, type Repository } from "@/lib/api";
import { decodeParams, loadRepository, type RepositoryParams } from "@/lib/repository-context";

/**
 * States the concrete reason asking is unavailable, checked in the order a user
 * would have to resolve them. Returns null once nothing blocks a question.
 */
function blockingReason(
  repositoryResult: ApiResult<Repository>,
  aiConfigured: boolean,
  embeddingsConfigured: boolean,
): string | null {
  if (!repositoryResult.ok) {
    return repositoryResult.error.code === "not_found"
      ? "Connect this repository before asking questions about it."
      : "The DevPilot API is not responding, so questions cannot be sent.";
  }
  if (repositoryResult.data.indexing_status !== "indexed") {
    return "Index this repository so DevPilot can retrieve the code an answer depends on.";
  }
  if (!embeddingsConfigured) {
    return "No embedding provider is configured, so the repository cannot be searched.";
  }
  if (!aiConfigured) {
    return "No language model is configured for this deployment.";
  }
  return null;
}

export default async function AskPage({ params }: { params: Promise<RepositoryParams> }) {
  const { owner, repo } = decodeParams(await params);
  const [repositoryResult, integrationsResult] = await Promise.all([
    loadRepository(owner, repo),
    getIntegrations(),
  ]);

  const integrations = integrationsResult.ok ? integrationsResult.data.integrations : [];
  const configured = (name: string) =>
    integrations.find((integration) => integration.name === name)?.configured ?? false;
  const aiConfigured = configured("ai_provider");
  const embeddingsConfigured = configured("embeddings");

  const repository = repositoryResult.ok ? repositoryResult.data : null;

  return (
    <AskWorkspace
      repository={repository}
      repositoryId={repository?.id ?? null}
      disabledReason={blockingReason(repositoryResult, aiConfigured, embeddingsConfigured)}
    />
  );
}
