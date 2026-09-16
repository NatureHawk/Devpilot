import { AskWorkspace } from "@/components/ask/ask-workspace";
import { getIntegrations, type ApiResult, type Repository } from "@/lib/api";
import { repositoryPath } from "@/lib/navigation";
import {
  decodeParams,
  loadConversations,
  loadRepository,
  type RepositoryParams,
} from "@/lib/repository-context";

type Blocker = { reason: string; action: { label: string; href: string } | null };

/**
 * The concrete reason asking is unavailable, in the order a user would resolve
 * them, with the action that resolves it when there is one.
 */
function blocker(
  owner: string,
  repo: string,
  repositoryResult: ApiResult<Repository>,
  aiConfigured: boolean,
  embeddingsConfigured: boolean,
): Blocker | null {
  if (!repositoryResult.ok) {
    return repositoryResult.error.code === "not_found"
      ? {
          reason: "DevPilot can only answer questions about repositories you connect.",
          action: { label: "Connect repository", href: "/repositories/connect" },
        }
      : { reason: "The DevPilot API isn't responding, so questions can't be sent.", action: null };
  }
  if (repositoryResult.data.indexing_status !== "indexed") {
    return {
      reason: "DevPilot answers from the indexed code, so this repository needs indexing first.",
      action: { label: "Index repository", href: repositoryPath(owner, repo) },
    };
  }
  if (!embeddingsConfigured) {
    return {
      reason: "No embedding provider is configured, so the code can't be searched.",
      action: { label: "Open settings", href: "/settings" },
    };
  }
  if (!aiConfigured) {
    return {
      reason: "No language model is configured for this deployment.",
      action: { label: "Open settings", href: "/settings" },
    };
  }
  return null;
}

export default async function AskPage({
  params,
  searchParams,
}: {
  params: Promise<RepositoryParams>;
  searchParams: Promise<{ conversation?: string | string[] }>;
}) {
  const { owner, repo } = decodeParams(await params);
  const { conversation } = await searchParams;
  const [repositoryResult, integrationsResult] = await Promise.all([
    loadRepository(owner, repo),
    getIntegrations(),
  ]);

  const integrations = integrationsResult.ok ? integrationsResult.data.integrations : [];
  const configured = (name: string) =>
    integrations.find((integration) => integration.name === name)?.configured ?? false;

  const repository = repositoryResult.ok ? repositoryResult.data : null;
  const conversations = repository ? await loadConversations(repository.id) : null;
  const blocked = blocker(
    owner,
    repo,
    repositoryResult,
    configured("ai_provider"),
    configured("embeddings"),
  );

  return (
    <AskWorkspace
      repositoryId={repository?.id ?? null}
      owner={owner}
      name={repo}
      disabledReason={blocked?.reason ?? null}
      blockedAction={blocked?.action ?? null}
      recentConversations={
        conversations?.ok
          ? conversations.data.items.map(({ id, title, created_at }) => ({ id, title, created_at }))
          : []
      }
      initialConversationId={typeof conversation === "string" ? conversation : null}
    />
  );
}
