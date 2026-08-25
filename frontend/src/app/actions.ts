"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";

import {
  connectRepository,
  getAuthorizeUrl,
  runIndex,
  SESSION_COOKIE,
  type ApiError,
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
      filesSkipped: run.files_skipped,
      parseFailures: run.parse_failures,
      complete: run.complete,
      commitSha: run.commit_sha,
    },
  };
}
