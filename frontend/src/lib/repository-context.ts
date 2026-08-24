import "server-only";

import { cache } from "react";

import { getRepository, type ApiResult, type Repository } from "@/lib/api";

/**
 * Loads a repository once per request.
 *
 * The workspace layout and the page inside it both need the record; `cache`
 * collapses that into a single API call for the render.
 */
export const loadRepository = cache((owner: string, repo: string): Promise<ApiResult<Repository>> =>
  getRepository(owner, repo),
);

export type RepositoryParams = { owner: string; repo: string };

/** Route params arrive URL-encoded; the API and UI both want them decoded. */
export function decodeParams(params: RepositoryParams): RepositoryParams {
  return { owner: decodeURIComponent(params.owner), repo: decodeURIComponent(params.repo) };
}
