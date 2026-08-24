import { ArrowRight, Plus } from "lucide-react";

import { Greeting } from "@/components/dashboard/greeting";
import { Pipeline } from "@/components/dashboard/pipeline";
import { PageBody, PageHeader, PageTitle } from "@/components/layout/page-header";
import { RepositoryList } from "@/components/repository/repository-list";
import { ButtonLink } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ApiErrorState } from "@/components/ui/error-state";
import { Panel, PanelHeader } from "@/components/ui/panel";
import { listRepositories } from "@/lib/api";

export default async function DashboardPage() {
  const result = await listRepositories();
  const repositories = result.ok ? result.data.items : [];

  return (
    <>
      <PageHeader>
        <PageTitle>Dashboard</PageTitle>
      </PageHeader>

      <PageBody>
        <div className="max-w-2xl">
          <Greeting />
          <p className="text-ink-muted mt-2 text-base">
            Choose a repository to start working with your codebase.
          </p>
        </div>

        <div className="mt-6 flex flex-wrap items-center gap-2">
          <ButtonLink href="/repositories/connect" variant="primary">
            <Plus aria-hidden="true" className="size-3.5" strokeWidth={2} />
            Connect repository
          </ButtonLink>
          <ButtonLink href="/repositories" variant="secondary">
            Browse repositories
          </ButtonLink>
        </div>

        {!result.ok ? <ApiErrorState error={result.error} className="mt-8" /> : null}

        <div className="mt-10 space-y-6">
          {repositories.length === 0 ? (
            <Panel>
              <div className="px-6 pt-7 pb-6">
                <h2 className="text-ink text-lg font-medium tracking-tight">
                  DevPilot works inside your repository
                </h2>
                <p className="text-ink-muted mt-2 max-w-2xl text-sm leading-relaxed">
                  It reads the code you already have, answers questions with the relevant files in
                  hand, and turns an accepted answer into a change you review before anything is
                  written back.
                </p>
              </div>
              <div className="border-line border-t">
                <Pipeline />
              </div>
              <div className="border-line flex items-center gap-3 border-t px-6 py-4">
                <ButtonLink href="/repositories/connect" variant="primary" size="sm">
                  Connect your first repository
                  <ArrowRight aria-hidden="true" className="size-3.5" strokeWidth={2} />
                </ButtonLink>
                <span className="text-ink-faint text-xs">
                  Requires a configured GitHub connection.
                </span>
              </div>
            </Panel>
          ) : null}

          <Panel>
            <PanelHeader
              title="Recent work"
              description="Repositories, conversations, proposed changes and pull requests."
              action={
                repositories.length > 0 ? (
                  <ButtonLink href="/repositories" variant="ghost" size="sm">
                    View all
                  </ButtonLink>
                ) : undefined
              }
            />
            {repositories.length > 0 ? (
              <RepositoryList repositories={repositories.slice(0, 5)} />
            ) : (
              <EmptyState
                title="Your recent work will appear here"
                description="Once a repository is connected, this is where DevPilot keeps the threads you have open: repositories you are working in, conversations about the code, changes waiting for review, and pull requests it has opened."
              />
            )}
          </Panel>
        </div>
      </PageBody>
    </>
  );
}
