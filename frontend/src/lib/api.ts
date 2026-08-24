import "server-only";

/**
 * Server-side client for the DevPilot API.
 *
 * Every call returns a discriminated result instead of throwing, so pages are
 * forced to render an explicit error state rather than falling through to a
 * blank screen. Only server components use this — the browser never talks to
 * the API directly, which keeps the backend origin out of the client bundle.
 */

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

/** Long enough for a cold backend, short enough that a page never hangs. */
const REQUEST_TIMEOUT_MS = 8000;

export type ApiErrorCode =
  | "not_found"
  | "conflict"
  | "integration_not_configured"
  | "service_unavailable"
  | "validation_error"
  | "http_error"
  | "internal_error"
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

async function request<T>(path: string): Promise<ApiResult<T>> {
  try {
    const response = await fetch(`${BACKEND_URL}${path}`, {
      headers: { Accept: "application/json" },
      // This data changes outside the request lifecycle; caching it would show
      // stale repository state after a connect or an index run.
      cache: "no-store",
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });

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

export type IndexingStatus = "not_indexed" | "queued" | "indexing" | "indexed" | "failed";
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
  created_at: string;
};

export type ListResponse<T> = { items: T[]; total: number };

export type IntegrationName = "github" | "ai_provider";

export type Integration = {
  name: IntegrationName;
  configured: boolean;
  description: string;
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
  return request<{ integrations: Integration[] }>("/api/v1/meta/integrations");
}
