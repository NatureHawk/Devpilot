import type { Metadata } from "next";
import type { ReactNode } from "react";

import { RepositoryHeader } from "@/components/repository/repository-header";
import { WorkspaceTabs } from "@/components/repository/workspace-tabs";
import { decodeParams, loadRepository, type RepositoryParams } from "@/lib/repository-context";

export async function generateMetadata({
  params,
}: {
  params: Promise<RepositoryParams>;
}): Promise<Metadata> {
  const { owner, repo } = decodeParams(await params);
  return { title: `${owner}/${repo}` };
}

/**
 * Workspace chrome shared by every repository tab. The record is loaded here so
 * the header can reflect real state; `loadRepository` is request-cached, so the
 * page below does not trigger a second call.
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

  return (
    <>
      <RepositoryHeader owner={owner} repo={repo} repository={repository} />
      <WorkspaceTabs owner={owner} repo={repo} />
      {children}
    </>
  );
}
