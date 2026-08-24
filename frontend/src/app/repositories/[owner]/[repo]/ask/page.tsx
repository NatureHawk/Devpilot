import { ContextPanel } from "@/components/ask/context-panel";
import { ContextRail } from "@/components/ask/context-rail";
import { ConversationPane } from "@/components/ask/conversation-pane";
import { getIntegrations, type ApiResult, type Repository } from "@/lib/api";
import { decodeParams, loadRepository, type RepositoryParams } from "@/lib/repository-context";

/**
 * States the concrete reason asking is unavailable, checked in the order a user
 * would have to resolve them. Returns null once nothing blocks a question.
 */
function blockingReason(
  repositoryResult: ApiResult<Repository>,
  aiConfigured: boolean,
): string | null {
  if (!repositoryResult.ok) {
    return repositoryResult.error.code === "not_found"
      ? "Connect this repository before asking questions about it."
      : "The DevPilot API is not responding, so questions cannot be sent.";
  }
  if (repositoryResult.data.indexing_status !== "indexed") {
    return "Index this repository so DevPilot can retrieve the code an answer depends on.";
  }
  if (!aiConfigured) {
    return "No AI provider is configured for this deployment.";
  }
  return null;
}

export default async function AskPage({ params }: { params: Promise<RepositoryParams> }) {
  const { owner, repo } = decodeParams(await params);
  const [repositoryResult, integrationsResult] = await Promise.all([
    loadRepository(owner, repo),
    getIntegrations(),
  ]);

  const aiConfigured = integrationsResult.ok
    ? (integrationsResult.data.integrations.find((i) => i.name === "ai_provider")?.configured ??
      false)
    : false;

  return (
    <div className="flex min-h-0 flex-1">
      <ContextRail repository={repositoryResult.ok ? repositoryResult.data : null} />
      <ConversationPane disabledReason={blockingReason(repositoryResult, aiConfigured)} />
      <ContextPanel />
    </div>
  );
}
