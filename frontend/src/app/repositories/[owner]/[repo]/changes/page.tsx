import { InvestigateWorkspace } from "@/components/changes/investigate-workspace";
import { NotConnectedState } from "@/components/repository/repository-states";
import { ApiErrorState } from "@/components/ui/error-state";
import { getIntegrations } from "@/lib/api";
import { repositoryPath } from "@/lib/navigation";
import {
  decodeParams,
  loadChanges,
  loadRepository,
  type RepositoryParams,
} from "@/lib/repository-context";

export default async function InvestigatePage({
  params,
  searchParams,
}: {
  params: Promise<RepositoryParams>;
  searchParams: Promise<{ request?: string | string[]; conversation?: string | string[] }>;
}) {
  const { owner, repo } = decodeParams(await params);
  const query = await searchParams;
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

  const repository = result.data;
  const [changesResult, integrationsResult] = await Promise.all([
    loadChanges(repository.id),
    getIntegrations(),
  ]);
  const integrations = integrationsResult.ok ? integrationsResult.data.integrations : [];
  const configured = (name: string) =>
    integrations.find((integration) => integration.name === name)?.configured ?? false;

  const blocked =
    repository.indexing_status !== "indexed"
      ? {
          reason:
            "DevPilot investigates the indexed code, so this repository needs indexing first.",
          action: { label: "Index repository", href: repositoryPath(owner, repo) },
        }
      : !configured("embeddings") || !configured("ai_provider")
        ? {
            reason:
              "A search provider and a language model must both be configured to investigate.",
            action: { label: "Open settings", href: "/settings" },
          }
        : null;

  return (
    <Body>
      {!changesResult.ok ? <ApiErrorState error={changesResult.error} className="mb-6" /> : null}
      <InvestigateWorkspace
        repositoryId={repository.id}
        owner={owner}
        name={repo}
        disabledReason={blocked?.reason ?? null}
        blockedAction={blocked?.action ?? null}
        initialRequest={typeof query.request === "string" ? query.request : ""}
        conversationId={typeof query.conversation === "string" ? query.conversation : null}
        recent={changesResult.ok ? changesResult.data.items : []}
      />
    </Body>
  );
}

function Body({ children }: { children: React.ReactNode }) {
  return (
    <main className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-6 py-8">{children}</div>
    </main>
  );
}
