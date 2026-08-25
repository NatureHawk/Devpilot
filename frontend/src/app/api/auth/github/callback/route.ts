import { NextResponse, type NextRequest } from "next/server";

import { exchangeGitHubCode, SESSION_COOKIE } from "@/lib/api";

/**
 * Where GitHub returns the browser after authorization.
 *
 * The code is exchanged server-side by the API (which holds the client secret),
 * and the resulting session is stored as an HttpOnly cookie on this origin. The
 * token itself never reaches client JavaScript.
 */

// 30 days, matching SESSION_MAX_AGE_SECONDS in the backend. If they disagree,
// the cookie outlives the signature and the user sees a silent sign-out.
const SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60;

function errorRedirect(request: NextRequest, reason: string): NextResponse {
  const url = new URL("/settings", request.url);
  url.searchParams.set("auth_error", reason);
  return NextResponse.redirect(url);
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  const params = request.nextUrl.searchParams;

  // GitHub reports a refused authorization here rather than by omitting `code`.
  if (params.get("error")) {
    return errorRedirect(request, "denied");
  }

  const code = params.get("code");
  const state = params.get("state");
  if (!code || !state) {
    return errorRedirect(request, "invalid_callback");
  }

  const result = await exchangeGitHubCode(code, state);
  if (!result.ok) {
    return errorRedirect(request, result.error.code);
  }

  const { session_token: sessionToken, redirect_path: redirectPath } = result.data;

  // Only same-site paths are honoured; the backend already rejects anything
  // else, and this is the second line of defence against an open redirect.
  const destination =
    redirectPath.startsWith("/") && !redirectPath.startsWith("//") ? redirectPath : "/";

  const response = NextResponse.redirect(new URL(destination, request.url));
  response.cookies.set({
    name: SESSION_COOKIE,
    value: sessionToken,
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_MAX_AGE_SECONDS,
    // Plain HTTP is normal in local development; anywhere else this must be TLS.
    secure: process.env.NODE_ENV === "production",
  });
  return response;
}
