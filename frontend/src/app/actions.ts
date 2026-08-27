"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";

import {
  connectRepository,
  createChange,
  getAuthorizeUrl,
  reviewChange,
  runIndex,
  searchRepository,
  SESSION_COOKIE,
  type ApiError,
  type ProposedChange,
  type SearchResponse,
} from "@/lib/api";
import { repositoryPath } from "@/lib/navigation";

/**
 * Server actions.
 *
 * Every mutation runs here rather than in the browser, so the session cookie
 * stays HttpOnly and the API origin never reaches the client bundle. Each
 * action returns a plain result the caller can render; none of them throw for
 * an expected failure.
 */

export type ActionResult<T = undefined> =
  { ok: true; data: T } | { ok: false; error: { code: string; message: string } };

function failure(error: ApiError): { ok: false; error: { code: string; message: string } } {
  return { ok: false, error: { code: error.code, message: error.message } };
}

export async function signInWithGitHub(redirectPath: string = "/"): Promise<ActionResult> {
  const result = await getAuthorizeUrl(redirectPath);
  if (!result.ok) return failure(result.error);

  // redirect() throws internally to unwind; it must sit outside the try/catch
  // of any caller that would swallow it.
  redirect(result.data.authorize_url);
}

export async function signOut(): Promise<void> {
  const store = await cookies();
  store.delete(SESSION_COOKIE);
  redirect("/");
}

export async function connectRepositoryAction(formData: FormData): Promise<ActionResult> {
  const raw = String(formData.get("repository") ?? "").trim();

  // Accept "owner/name" or a full GitHub URL, which is what people actually paste.
  const cleaned = raw
    .replace(/^https?:\/\/(www\.)?github\.com\//i, "")
    .replace(/\.git$/i, "")
    .replace(/\/+$/, "");
  const [owner, name] = cleaned.split("/");

  if (!owner || !name || cleaned.split("/").length !== 2) {
    return {
      ok: false,
      error: {
        code: "validation_error",
        message: "Enter a repository as owner/name, or paste its GitHub URL.",
      },
    };
  }

  const result = await connectRepository(owner, name);
  if (!result.ok) return failure(result.error);

  revalidatePath("/repositories");
  redirect(repositoryPath(result.data.owner, result.data.name));
}

export type IndexOutcome = {
  filesIndexed: number;
  filesParsed: number;
  chunksCreated: number;
  chunksEmbedded: number;
  filesSkipped: number;
  parseFailures: number;
  complete: boolean;
  commitSha: string | null;
};

export async function indexRepositoryAction(
  repositoryId: string,
  owner: string,
  name: string,
): Promise<ActionResult<IndexOutcome>> {
  const result = await runIndex(repositoryId);
  if (!result.ok) return failure(result.error);

  // The workspace header and every tab read indexing state, so revalidate the
  // whole repository subtree rather than one page.
  revalidatePath(repositoryPath(owner, name), "layout");

  const run = result.data;
  return {
    ok: true,
    data: {
      filesIndexed: run.files_indexed,
      filesParsed: run.files_parsed,
      chunksCreated: run.chunks_created,
      chunksEmbedded: run.chunks_embedded,
      filesSkipped: run.files_skipped,
      parseFailures: run.parse_failures,
      complete: run.complete,
      commitSha: run.commit_sha,
    },
  };
}

/**
 * Retrieval only — this action embeds the query and ranks stored vectors. No
 * language model is called, and nothing is written to the database.
 */
export async function searchRepositoryAction(
  repositoryId: string,
  query: string,
  topK = 8,
): Promise<ActionResult<SearchResponse>> {
  const trimmed = query.trim();
  if (!trimmed) {
    return {
      ok: false,
      error: { code: "validation_error", message: "Enter a question to search for." },
    };
  }

  const result = await searchRepository(repositoryId, trimmed, topK);
  if (!result.ok) return failure(result.error);
  return { ok: true, data: result.data };
}

/**
 * Investigate a change request and produce a proposal.
 *
 * Runs the bounded tool loop server-side and returns a patch for review.
 * Nothing is written to GitHub — approval is an application state.
 */
export async function proposeChangeAction(
  repositoryId: string,
  owner: string,
  name: string,
  request: string,
): Promise<ActionResult<ProposedChange>> {
  const trimmed = request.trim();
  if (!trimmed) {
    return {
      ok: false,
      error: { code: "validation_error", message: "Describe the change you want." },
    };
  }

  const result = await createChange(repositoryId, trimmed);
  if (!result.ok) return failure(result.error);

  revalidatePath(repositoryPath(owner, name, "changes"));
  return { ok: true, data: result.data };
}

/** Record a human decision on a proposal. */
export async function reviewChangeAction(
  changeId: string,
  decision: "approve" | "reject",
  owner: string,
  name: string,
): Promise<ActionResult<ProposedChange>> {
  const result = await reviewChange(changeId, decision);
  if (!result.ok) return failure(result.error);

  revalidatePath(repositoryPath(owner, name, "changes"));
  return { ok: true, data: result.data };
}
