import { ChevronRight } from "lucide-react";

import { IndexPanel, type IndexSummary } from "@/components/repository/index-panel";
import { RecentWork } from "@/components/repository/recent-work";
import { NotConnectedState } from "@/components/repository/repository-states";
import { SearchInspector } from "@/components/repository/search-inspector";
import { searchRepositoryAction } from "@/app/actions";
import { ApiErrorState } from "@/components/ui/error-state";
import { JourneyTrack } from "@/components/ui/journey";
import { getLanguages, type Repository } from "@/lib/api";
import { repositoryPath } from "@/lib/navigation";
import {
  decodeParams,
  loadRepository,
  loadWorkflowContext,
  type RepositoryParams,
} from "@/lib/repository-context";
import { deriveJourney } from "@/lib/workflow";

/**
 * Repository overview: where the repository is in its journey → index state
 * and the next step → recent work → diagnostics for developers who want them.
 */
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
  const indexed = repository.indexing_status === "indexed";
  const [context, languages] = await Promise.all([
    loadWorkflowContext(repository),
    indexed ? getLanguages(repository.id) : Promise.resolve(null),
  ]);

  // Bound here so the client component cannot search a different repository.
  const repositoryId = repository.id;
  async function searchAction(query: string) {
    "use server";
    return searchRepositoryAction(repositoryId, query);
  }

  return (
    <Body>
      <div className="space-y-10">
        <section aria-labelledby="journey-heading">
          <h2
            id="journey-heading"
            className="text-ink-faint text-xs font-semibold tracking-[0.08em] uppercase"
          >
            Your progress
          </h2>
          <JourneyTrack
            className="mt-2"
            steps={deriveJourney(context.workflow, repository.indexing_status)}
            hrefFor={(step) => repositoryPath(owner, repo, step.segment)}
          />
        </section>

        <IndexPanel
          repositoryId={repository.id}
          owner={owner}
          name={repo}
          summary={toSummary(repository)}
          next={context.workflow.next}
          languages={languages?.ok ? languages.data : []}
        />

        <RecentWork
          owner={owner}
          repo={repo}
          conversations={context.conversations}
          changes={context.changes}
        />

        {indexed ? (
          <details className="group">
            <summary className="text-ink-muted hover:text-ink flex w-fit cursor-pointer list-none items-center gap-1.5 rounded-md text-sm transition-colors">
              <ChevronRight
                aria-hidden="true"
                className="size-3.5 transition-transform group-open:rotate-90"
                strokeWidth={2}
              />
              Retrieval diagnostics
              <span className="text-ink-faint text-xs">— for developers</span>
            </summary>
            <p className="text-ink-muted mt-2 max-w-2xl text-sm">
              See exactly which code a question retrieves — semantic and keyword candidates, scores
              and the sources an answer would receive — without generating an answer.
            </p>
            <div className="mt-4">
              <SearchInspector action={searchAction} />
            </div>
          </details>
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
    <main className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-6 py-8">{children}</div>
    </main>
  );
}
