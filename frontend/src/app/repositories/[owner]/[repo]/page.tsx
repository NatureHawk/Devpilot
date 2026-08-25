import { IndexPanel, type IndexSummary } from "@/components/repository/index-panel";
import { NotConnectedState } from "@/components/repository/repository-states";
import { ApiErrorState } from "@/components/ui/error-state";
import { Panel, PanelHeader } from "@/components/ui/panel";
import { getLanguages, type LanguageCount, type Repository } from "@/lib/api";
import { decodeParams, loadRepository, type RepositoryParams } from "@/lib/repository-context";

export default async function RepositoryOverviewPage({
  params,
}: {
  params: Promise<RepositoryParams>;
}) {
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

  const repository = result.data;
  // Only meaningful once something has been indexed; skipped otherwise so an
  // unindexed workspace makes one request instead of two.
  const languages =
    repository.indexing_status === "indexed" ? await getLanguages(repository.id) : null;

  return (
    <Body>
      <div className="space-y-6">
        <IndexPanel
          repositoryId={repository.id}
          owner={owner}
          name={repo}
          summary={toSummary(repository)}
        />

        {languages?.ok && languages.data.length > 0 ? (
          <LanguagePanel languages={languages.data} />
        ) : null}
      </div>
    </Body>
  );
}

function toSummary(repository: Repository): IndexSummary {
  return {
    status: repository.indexing_status,
    commitSha: repository.indexed_commit_sha,
    indexedAt: repository.indexed_at,
    fileCount: repository.indexed_file_count,
    parsedFileCount: repository.indexed_parsed_file_count,
    chunkCount: repository.indexed_chunk_count,
    error: repository.indexing_error,
  };
}

function Body({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-6 py-8">{children}</div>
    </div>
  );
}

function LanguagePanel({ languages }: { languages: LanguageCount[] }) {
  const total = languages.reduce((sum, entry) => sum + entry.file_count, 0);

  return (
    <Panel>
      <PanelHeader title="Languages" description="Indexed files by detected language." />
      <ul className="divide-line divide-y">
        {languages.map((entry) => (
          <li key={entry.language} className="flex items-center gap-4 px-4 py-2.5">
            <span className="text-ink w-40 shrink-0 text-xs font-medium">{entry.language}</span>
            <span
              aria-hidden="true"
              className="bg-line h-1 min-w-0 flex-1 overflow-hidden rounded-full"
            >
              <span
                className="bg-accent block h-full rounded-full"
                style={{ width: `${Math.max(2, (entry.file_count / total) * 100)}%` }}
              />
            </span>
            <span className="text-ink-muted w-12 shrink-0 text-right font-mono text-xs">
              {entry.file_count}
            </span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
