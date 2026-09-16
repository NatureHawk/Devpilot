import "server-only";

import { cache } from "react";

import {
  getRepository,
  listChanges,
  listConversations,
  type ApiResult,
  type Conversation,
  type ProposedChange,
  type Repository,
} from "@/lib/api";
import { deriveWorkflow, type Workflow } from "@/lib/workflow";

/**
 * Per-request loaders.
 *
 * The repository layout (for the workflow sidebar) and the page inside it read
 * the same records; `cache` collapses those into one API call per render.
 */
export const loadRepository = cache((owner: string, repo: string): Promise<ApiResult<Repository>> =>
  getRepository(owner, repo),
);

export const loadChanges = cache((repositoryId: string) => listChanges(repositoryId));

export const loadConversations = cache((repositoryId: string) => listConversations(repositoryId));

export type WorkflowContext = {
  workflow: Workflow;
  changes: ProposedChange[];
  conversations: Conversation[];
};

/**
 * The repository's workflow state, from its real records.
 *
 * A failed secondary read counts as "nothing yet" — it can make a step look
 * unstarted, never falsely complete.
 */
export async function loadWorkflowContext(repository: Repository): Promise<WorkflowContext> {
  const [changes, conversations] = await Promise.all([
    loadChanges(repository.id),
    loadConversations(repository.id),
  ]);
  const changeItems = changes.ok ? changes.data.items : [];
  const conversationItems = conversations.ok ? conversations.data.items : [];

  return {
    workflow: deriveWorkflow({
      repositoryName: repository.name,
      indexingStatus: repository.indexing_status,
      conversationCount: conversationItems.length,
      changeStatuses: changeItems.map((change) => change.status),
    }),
    changes: changeItems,
    conversations: conversationItems,
  };
}

export type RepositoryParams = { owner: string; repo: string };

/** Route params arrive URL-encoded; the API and UI both want them decoded. */
export function decodeParams(params: RepositoryParams): RepositoryParams {
  return { owner: decodeURIComponent(params.owner), repo: decodeURIComponent(params.repo) };
}
