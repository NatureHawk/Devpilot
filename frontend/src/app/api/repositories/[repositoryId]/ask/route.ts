import { cookies } from "next/headers";

import { SESSION_COOKIE } from "@/lib/api";

/**
 * Streams an answer from the API to the browser.
 *
 * A route handler rather than a server action: server actions resolve to a
 * single value, and this response is incremental. The handler exists so the
 * session cookie stays HttpOnly — the browser never holds the token, and the
 * backend origin never reaches the client bundle.
 *
 * The upstream body is piped through untouched, so what the browser reads is
 * genuinely the model's output as it arrives.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ repositoryId: string }> },
) {
  const { repositoryId } = await params;
  const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";

  const store = await cookies();
  const token = store.get(SESSION_COOKIE)?.value;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  let upstream: Response;
  try {
    upstream = await fetch(
      `${backendUrl}/api/v1/repositories/${encodeURIComponent(repositoryId)}/ask`,
      { method: "POST", headers, body: await request.text() },
    );
  } catch {
    return Response.json(
      { error: { code: "unreachable", message: "The DevPilot API is not responding." } },
      { status: 502 },
    );
  }

  // A failure before the stream starts is still a normal JSON error, so the
  // client can render it the same way it renders every other API failure.
  if (!upstream.ok || !upstream.body) {
    const body = await upstream.text();
    return new Response(body, {
      status: upstream.status,
      headers: { "Content-Type": "application/json" },
    });
  }

  return new Response(upstream.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
