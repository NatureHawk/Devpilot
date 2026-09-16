import { Plus } from "lucide-react";
import type { Metadata } from "next";

import { SignInPrompt } from "@/components/auth/sign-in-prompt";
import { PageBody, PageHeader, PageTitle } from "@/components/layout/page-header";
import { RepositoryList, type RepositoryEntry } from "@/components/repository/repository-list";
import { WorkflowOverview } from "@/components/repository/workflow-overview";
import { ButtonLink } from "@/components/ui/button";
import { ApiErrorState } from "@/components/ui/error-state";
import { NextStep } from "@/components/ui/next-step";
import { getIntegrations, listRepositories, type Repository } from "@/lib/api";
import { loadWorkflowContext } from "@/lib/repository-context";

export const metadata: Metadata = { title: "Repositories" };

/**
 * The starting point. It always answers one question: what do I do next?
 *
 * Signed out → connect GitHub. No repositories → connect one. Otherwise →
 * continue the most recent repository, with every other one showing how far it
 * has come and its next step.
 */
export default async function HomePage() {
  const result = await listRepositories();

  if (!result.ok && result.error.code === "not_authenticated") {
    const integrations = await getIntegrations();
    const github = integrations.ok
      ? integrations.data.integrations.find((integration) => integration.name === "github")
      : undefined;

    return (
      <>
        <PageHeader>
          <PageTitle>Welcome</PageTitle>
        </PageHeader>
        <PageBody>
          <div className="max-w-2xl">
            <h1 className="text-ink text-2xl font-semibold tracking-tight">
              Understand a codebase, then change it safely.
            </h1>
            <p className="text-ink-muted mt-2 text-base leading-relaxed">
              DevPilot reads a GitHub repository, answers questions with the exact files and lines
              behind each answer, and turns what you learn into a pull request you have reviewed.
            </p>
            <SignInPrompt
              className="mt-6"
              redirectPath="/"
              configured={github?.configured ?? false}
            />
            <WorkflowOverview className="mt-10" />
          </div>
        </PageBody>
      </>
    );
  }

  if (!result.ok) {
    return (
      <>
        <PageHeader>
          <PageTitle>Repositories</PageTitle>
        </PageHeader>
        <PageBody>
          <ApiErrorState error={result.error} />
        </PageBody>
      </>
    );
  }

  const entries = await withWorkflows(result.data.items);

  return (
    <>
      <PageHeader>
        <PageTitle>Repositories</PageTitle>
        {entries.length > 0 ? (
          <ButtonLink href="/repositories/connect" variant="ghost" size="sm" className="ml-auto">
            <Plus aria-hidden="true" className="size-3.5" strokeWidth={2} />
            Connect repository
          </ButtonLink>
        ) : null}
      </PageHeader>

      <PageBody>
        {entries.length === 0 ? (
          <div className="max-w-2xl">
            <NextStep
              eyebrow="Get started"
              title="Connect your first repository"
              description="Choose a GitHub repository you work in. DevPilot reads it through GitHub — nothing is written back unless you approve a change and create its pull request."
              action={
                <ButtonLink href="/repositories/connect" variant="primary" size="lg" forward>
                  Connect repository
                </ButtonLink>
              }
            />
            <WorkflowOverview className="mt-10" />
          </div>
        ) : (
          <div className="max-w-3xl">
            <RepositoryList entries={entries} />
          </div>
        )}
      </PageBody>
    </>
  );
}

/**
 * Each repository with its workflow, most recently active first. Activity is
 * the latest real timestamp on the repository or anything made in it.
 */
async function withWorkflows(repositories: Repository[]): Promise<RepositoryEntry[]> {
  const loaded = await Promise.all(
    repositories.map(async (repository) => {
      const context = await loadWorkflowContext(repository);
      const timestamps = [
        repository.created_at,
        repository.indexed_at,
        ...context.conversations.map((conversation) => conversation.created_at),
        ...context.changes.flatMap((change) => [
          change.created_at,
          change.reviewed_at,
          change.executed_at,
        ]),
      ];
      const lastActivity = Math.max(
        0,
        ...timestamps.map((value) => (value ? Date.parse(value) || 0 : 0)),
      );
      return { entry: { repository, workflow: context.workflow }, lastActivity };
    }),
  );
  return loaded.sort((a, b) => b.lastActivity - a.lastActivity).map(({ entry }) => entry);
}
