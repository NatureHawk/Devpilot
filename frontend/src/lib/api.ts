import "server-only";

import { cookies } from "next/headers";

/**
 * Server-side client for the DevPilot API.
 *
 * Every call returns a discriminated result instead of throwing, so pages are
 * forced to render an explicit error state rather than falling through to a
 * blank screen. Only server components and server actions use this — the
 * browser never talks to the API directly, which keeps the backend origin and
 * the session out of the client bundle.
 */

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

export const SESSION_COOKIE = "devpilot_session";

/** Long enough for a cold backend, short enough that a page never hangs. */
const REQUEST_TIMEOUT_MS = 8000;

/**
 * Indexing is synchronous: the request is held open while a whole repository is
 * fetched, parsed and stored, so it needs a budget measured in minutes rather
 * than the seconds a page read gets.
 */
const INDEXING_TIMEOUT_MS = 10 * 60 * 1000;

export type ApiErrorCode =
  | "not_found"
  | "conflict"
  | "integration_not_configured"
  | "service_unavailable"
  | "validation_error"
  | "http_error"
  | "internal_error"
  | "not_authenticated"
  | "indexing_in_progress"
  | "indexing_failed"
  | "github_unauthorized"
  | "github_forbidden"
  | "github_rate_limited"
  | "github_not_found"
  | "github_unavailable"
  | "github_error"
  /** The API could not be reached at all — distinct from any API-reported failure. */
  | "unreachable";

export type ApiError = {
  code: ApiErrorCode;
  message: string;
  status: number;
};

export type ApiResult<T> = { ok: true; data: T } | { ok: false; error: ApiError };

type ApiErrorEnvelope = {
  error?: { code?: string; message?: string };
};

type RequestOptions = {
  method?: "GET" | "POST";
  body?: unknown;
  timeoutMs?: number;
  /** Send the caller's session. Off for genuinely public reads. */
  authenticated?: boolean;
};

async function sessionToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(SESSION_COOKIE)?.value ?? null;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<ApiResult<T>> {
  const { method = "GET", body, timeoutMs = REQUEST_TIMEOUT_MS, authenticated = true } = options;

  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  if (authenticated) {
    // The API is stateless: Next holds the HttpOnly cookie and forwards it as a
    // bearer token, so the backend never depends on cookie domains.
    const token = await sessionToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }

  try {
    const response = await fetch(`${BACKEND_URL}${path}`, {
      method,
      headers,
      // exactOptionalPropertyTypes rejects an explicit undefined body, so the
      // key is only present when there is one.
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      // This data changes outside the request lifecycle; caching it would show
      // stale repository state after a connect or an index run.
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
    });

    if (response.status === 204) return { ok: true, data: undefined as T };

    if (!response.ok) {
      const envelope = (await response.json().catch(() => ({}))) as ApiErrorEnvelope;
      return {
        ok: false,
        error: {
          code: (envelope.error?.code as ApiErrorCode) ?? "http_error",
          message: envelope.error?.message ?? `Request failed with status ${response.status}.`,
          status: response.status,
        },
      };
    }

    return { ok: true, data: (await response.json()) as T };
  } catch {
    // Connection refused, DNS failure or timeout: the API is not answering.
    return {
      ok: false,
      error: {
        code: "unreachable",
        message: "The DevPilot API is not responding.",
        status: 0,
      },
    };
  }
}

export type IndexingStatus = "not_indexed" | "indexing" | "indexed" | "failed";
export type RepositoryVisibility = "public" | "private";

export type Repository = {
  id: string;
  provider: string;
  owner: string;
  name: string;
  default_branch: string;
  visibility: RepositoryVisibility;
  indexing_status: IndexingStatus;
  indexed_at: string | null;
  indexed_commit_sha: string | null;
  indexing_started_at: string | null;
  indexing_error: string | null;
  indexed_file_count: number;
  indexed_parsed_file_count: number;
  indexed_chunk_count: number;
  created_at: string;
};

export type ListResponse<T> = { items: T[]; total: number };

export type IntegrationName = "github" | "embeddings" | "ai_provider";

export type Integration = {
  name: IntegrationName;
  configured: boolean;
  description: string;
};

export type CurrentUser = {
  id: string;
  github_login: string | null;
  display_name: string | null;
  avatar_url: string | null;
  has_github_token: boolean;
};

export type IndexRun = {
  repository_id: string;
  status: IndexingStatus;
  commit_sha: string | null;
  files_discovered: number;
  files_indexed: number;
  files_parsed: number;
  chunks_created: number;
  chunks_embedded: number;
  embedding_model: string | null;
  files_skipped: number;
  skipped_by_reason: Record<string, number>;
  parse_failures: number;
  complete: boolean;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
};

export type LanguageCount = { language: string; file_count: number };

/** One retrieval candidate with the signals that ranked it. None of these are probabilities. */
export type RetrievalCandidate = {
  chunk_id: string;
  file_path: string;
  language: string;
  symbol: string | null;
  parent_symbol: string | null;
  chunk_type: string;
  start_line: number;
  end_line: number;
  /** Cosine similarity in [-1, 1]; a ranking signal. */
  semantic_score: number;
  semantic_rank: number | null;
  lexical_rank: number | null;
  /** Share of the question's informative term weight matched, 0–1. */
  lexical_score: number;
  matched_terms: string[];
  exact_match: "symbol" | "path" | "identifier" | null;
  /** Position in the fused ranking; null for a de-duplicated copy. */
  final_rank: number | null;
  /** Comments or punctuation only; never used as evidence. */
  low_value: boolean;
};

/** A source selected into the answer context. */
export type SearchResult = RetrievalCandidate & {
  content: string;
  /** Same as semantic_score. */
  score: number;
};

export type RetrievalStrength = "none" | "weak" | "useful";

export type SearchResponse = {
  /** What an answer would receive, in citation order. */
  results: SearchResult[];
  semantic_candidates: RetrievalCandidate[];
  lexical_candidates: RetrievalCandidate[];
  strength: RetrievalStrength;
  model: string;
  searched_chunks: number;
  query_terms: string[];
  lexical_available: boolean;
  context_chars: number;
  context_budget: number;
  max_sources: number;
};

export function listRepositories(): Promise<ApiResult<ListResponse<Repository>>> {
  return request<ListResponse<Repository>>("/api/v1/repositories");
}

export function getRepository(owner: string, name: string): Promise<ApiResult<Repository>> {
  return request<Repository>(
    `/api/v1/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(name)}`,
  );
}

export function getIntegrations(): Promise<ApiResult<{ integrations: Integration[] }>> {
  return request<{ integrations: Integration[] }>("/api/v1/meta/integrations", {
    authenticated: false,
  });
}

export function getCurrentUser(): Promise<ApiResult<CurrentUser | null>> {
  return request<CurrentUser | null>("/api/v1/auth/me");
}

export function getAuthorizeUrl(
  redirectPath: string,
): Promise<ApiResult<{ authorize_url: string }>> {
  return request<{ authorize_url: string }>(
    `/api/v1/auth/github/authorize?redirect_path=${encodeURIComponent(redirectPath)}`,
    { authenticated: false },
  );
}

export type SessionResponse = {
  session_token: string;
  redirect_path: string;
  user: CurrentUser;
};

export function exchangeGitHubCode(
  code: string,
  state: string,
): Promise<ApiResult<SessionResponse>> {
  return request<SessionResponse>("/api/v1/auth/github/callback", {
    method: "POST",
    body: { code, state },
    authenticated: false,
  });
}

export function connectRepository(owner: string, name: string): Promise<ApiResult<Repository>> {
  return request<Repository>("/api/v1/repositories", {
    method: "POST",
    body: { owner, name },
  });
}

export function runIndex(repositoryId: string): Promise<ApiResult<IndexRun>> {
  return request<IndexRun>(`/api/v1/repositories/${repositoryId}/index`, {
    method: "POST",
    timeoutMs: INDEXING_TIMEOUT_MS,
  });
}

export function searchRepository(
  repositoryId: string,
  query: string,
  topK?: number,
): Promise<ApiResult<SearchResponse>> {
  return request<SearchResponse>(`/api/v1/repositories/${repositoryId}/search`, {
    method: "POST",
    // Omitted top_k lets the server's CONTEXT_MAX_SOURCES decide.
    body: topK === undefined ? { query } : { query, top_k: topK },
  });
}

export type ChangeStatus =
  | "proposed"
  | "approved"
  | "rejected"
  | "stale"
  | "failed"
  | "executing"
  | "committed"
  | "pr_created";

export type ToolActivity = {
  tool: string;
  duration_ms: number;
  ok: boolean;
  detail: string;
};

export type ExecutionEvent = {
  event: string;
  at: string;
};

export type ProposedChange = {
  id: string;
  repository_id: string;
  conversation_id: string | null;
  status: ChangeStatus;
  request: string;
  summary: string;
  diff: string;
  files_changed: number;
  tool_calls_used: number;
  indexed_commit_sha: string | null;
  model: string | null;
  error: string | null;
  investigation: ToolActivity[];
  created_at: string;
  reviewed_at: string | null;
  branch_name: string | null;
  commit_sha: string | null;
  pr_number: number | null;
  pr_url: string | null;
  executed_at: string | null;
  execution_error: string | null;
  execution_events: ExecutionEvent[];
};

export function listChanges(
  repositoryId: string,
): Promise<ApiResult<ListResponse<ProposedChange>>> {
  return request<ListResponse<ProposedChange>>(`/api/v1/repositories/${repositoryId}/changes`);
}

export function createChange(
  repositoryId: string,
  changeRequest: string,
  conversationId?: string,
): Promise<ApiResult<ProposedChange>> {
  return request<ProposedChange>(`/api/v1/repositories/${repositoryId}/changes`, {
    method: "POST",
    // Linking the conversation records which question the investigation grew from.
    body: conversationId
      ? { request: changeRequest, conversation_id: conversationId }
      : { request: changeRequest },
    // Investigation runs several model turns; it needs the indexing budget,
    // not the default request budget.
    timeoutMs: INDEXING_TIMEOUT_MS,
  });
}

export function reviewChange(
  changeId: string,
  decision: "approve" | "reject",
): Promise<ApiResult<ProposedChange>> {
  return request<ProposedChange>(`/api/v1/changes/${changeId}/${decision}`, { method: "POST" });
}

export function executeChange(changeId: string): Promise<ApiResult<ProposedChange>> {
  return request<ProposedChange>(`/api/v1/changes/${changeId}/execute`, {
    method: "POST",
    // Several GitHub API calls in sequence (blobs, tree, commit, branch, PR);
    // the same budget indexing uses, not the default request timeout.
    timeoutMs: INDEXING_TIMEOUT_MS,
  });
}

export function getLanguages(repositoryId: string): Promise<ApiResult<LanguageCount[]>> {
  return request<LanguageCount[]>(`/api/v1/repositories/${repositoryId}/languages`);
}

/** A citation stored with a past answer. Source text is not stored with it. */
export type ConversationSource = {
  label: string;
  chunk_id: string | null;
  file_path: string;
  symbol: string | null;
  start_line: number;
  end_line: number;
  language: string | null;
  score: number;
};

export type ConversationMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  model: string | null;
  created_at: string;
  sources: ConversationSource[];
};

export type Conversation = {
  id: string;
  repository_id: string;
  title: string | null;
  created_at: string;
  /** Empty in list responses; populated when one conversation is read. */
  messages: ConversationMessage[];
};

export function listConversations(
  repositoryId: string,
): Promise<ApiResult<ListResponse<Conversation>>> {
  return request<ListResponse<Conversation>>(`/api/v1/repositories/${repositoryId}/conversations`);
}

export function getConversation(
  repositoryId: string,
  conversationId: string,
): Promise<ApiResult<Conversation>> {
  return request<Conversation>(
    `/api/v1/repositories/${repositoryId}/conversations/${encodeURIComponent(conversationId)}`,
  );
}
