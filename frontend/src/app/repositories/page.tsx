import { Plus } from "lucide-react";
import type { Metadata } from "next";

import { SignInPrompt } from "@/components/auth/sign-in-prompt";
import { PageBody, PageHeader, PageTitle } from "@/components/layout/page-header";
import { RepositoryList } from "@/components/repository/repository-list";
import { ButtonLink } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ApiErrorState } from "@/components/ui/error-state";
import { Panel, PanelHeader } from "@/components/ui/panel";
import { getIntegrations, listRepositories } from "@/lib/api";

export const metadata: Metadata = { title: "Repositories" };

export default async function RepositoriesPage() {
  const result = await listRepositories();

  // Signing in is a normal starting state, not an error to report.
  if (!result.ok && result.error.code === "not_authenticated") {
    const integrations = await getIntegrations();
    const github = integrations.ok
      ? integrations.data.integrations.find((integration) => integration.name === "github")
      : undefined;

    return (
      <>
        <PageHeader>
          <PageTitle>Repositories</PageTitle>
        </PageHeader>
        <PageBody>
          <SignInPrompt redirectPath="/repositories" configured={github?.configured ?? false} />
        </PageBody>
      </>
    );
  }

  return (
    <>
      <PageHeader>
        <PageTitle>Repositories</PageTitle>
        <ButtonLink href="/repositories/connect" variant="primary" size="sm" className="ml-auto">
          <Plus aria-hidden="true" className="size-3.5" strokeWidth={2} />
          Connect
        </ButtonLink>
      </PageHeader>

      <PageBody>
        {!result.ok ? (
          <ApiErrorState error={result.error} />
        ) : (
          <Panel>
            <PanelHeader
              title="Connected repositories"
              description={
                result.data.total === 1 ? "1 repository" : `${result.data.total} repositories`
              }
            />
            {result.data.items.length > 0 ? (
              <RepositoryList repositories={result.data.items} />
            ) : (
              <EmptyState
                title="No repositories connected"
                description="Connect a repository to give DevPilot something to read. Everything else — questions, proposed changes, pull requests — starts from here."
              >
                <ButtonLink href="/repositories/connect" variant="primary" size="sm">
                  Connect repository
                </ButtonLink>
              </EmptyState>
            )}
          </Panel>
        )}
      </PageBody>
    </>
  );
}
