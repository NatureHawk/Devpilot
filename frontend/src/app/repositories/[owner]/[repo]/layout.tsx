import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AppShell } from "@/components/layout/app-shell";
import { WorkflowNav } from "@/components/layout/workflow-nav";
import { RepositoryHeader } from "@/components/repository/repository-header";
import {
  decodeParams,
  loadRepository,
  loadWorkflowContext,
  type RepositoryParams,
} from "@/lib/repository-context";

export async function generateMetadata({
  params,
}: {
  params: Promise<RepositoryParams>;
}): Promise<Metadata> {
  const { repo } = decodeParams(await params);
  return { title: repo };
}

/**
 * Chrome shared by every screen inside a repository.
 *
 * The sidebar carries the repository's workflow, derived from its real
 * records, so every screen shows what is done and what comes next. Loaders are
 * request-cached, so pages below reuse these reads.
 */
export default async function RepositoryLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<RepositoryParams>;
}) {
  const { owner, repo } = decodeParams(await params);
  const result = await loadRepository(owner, repo);
  const repository = result.ok ? result.data : null;
  const context = repository ? await loadWorkflowContext(repository) : null;

  return (
    <AppShell
      workflowNav={
        context ? (
          <WorkflowNav owner={owner} repo={repo} stages={context.workflow.stages} />
        ) : undefined
      }
    >
      <RepositoryHeader owner={owner} repo={repo} repository={repository} />
      {children}
    </AppShell>
  );
}
