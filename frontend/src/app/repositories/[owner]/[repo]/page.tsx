import { NotConnectedState, NotIndexedState } from "@/components/repository/repository-states";
import { ApiErrorState } from "@/components/ui/error-state";
import { Panel, PanelHeader } from "@/components/ui/panel";
import { decodeParams, loadRepository, type RepositoryParams } from "@/lib/repository-context";

export default async function RepositoryOverviewPage({
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
          result.data.indexing_status === "indexed" ? (
            <RepositoryMetadata
              provider={result.data.provider}
              defaultBranch={result.data.default_branch}
              visibility={result.data.visibility}
              indexedAt={result.data.indexed_at}
            />
          ) : (
            <NotIndexedState />
          )
        ) : result.error.code === "not_found" ? (
          <NotConnectedState owner={owner} repo={repo} />
        ) : (
          <ApiErrorState error={result.error} />
        )}
      </div>
    </div>
  );
}

/** Facts from the stored record. Nothing here is derived or estimated. */
function RepositoryMetadata({
  provider,
  defaultBranch,
  visibility,
  indexedAt,
}: {
  provider: string;
  defaultBranch: string;
  visibility: string;
  indexedAt: string | null;
}) {
  const rows: [string, string][] = [
    ["Provider", provider],
    ["Default branch", defaultBranch],
    ["Visibility", visibility],
    ["Last indexed", indexedAt ? new Date(indexedAt).toLocaleString() : "Never"],
  ];

  return (
    <Panel>
      <PanelHeader title="Repository" description="Details DevPilot has recorded." />
      <dl className="divide-line divide-y">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-center gap-4 px-4 py-2.5">
            <dt className="text-ink-muted w-40 shrink-0 text-xs">{label}</dt>
            <dd className="text-ink min-w-0 truncate font-mono text-xs">{value}</dd>
          </div>
        ))}
      </dl>
    </Panel>
  );
}
