"use client";

import { useState, useTransition } from "react";

import { signInWithGitHub } from "@/app/actions";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import { cn } from "@/lib/cn";

/**
 * Sign-in gate.
 *
 * When GitHub OAuth is not configured for the deployment, the button is not
 * offered at all — a control that cannot work is worse than an explanation.
 */
export function SignInPrompt({
  redirectPath,
  configured,
  className,
}: {
  redirectPath: string;
  configured: boolean;
  className?: string;
}) {
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  const start = () => {
    setError(null);
    startTransition(async () => {
      const result = await signInWithGitHub(redirectPath);
      if (result && !result.ok) setError(result.error.message);
    });
  };

  return (
    <Panel className={cn("max-w-2xl", className)}>
      <div className="px-5 py-5">
        <h3 className="text-ink text-sm font-medium">Sign in with GitHub</h3>
        <p className="text-ink-muted mt-1.5 text-sm leading-relaxed">
          {configured
            ? "DevPilot needs your authorisation to read repository contents on your behalf. It never writes without an approved review."
            : "This deployment has no GitHub credentials. Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET in the backend environment, then restart the API."}
        </p>

        {configured ? (
          <div className="mt-4">
            <Button variant="primary" size="sm" onClick={start} disabled={pending}>
              {pending ? "Redirecting…" : "Continue with GitHub"}
            </Button>
          </div>
        ) : null}

        {error ? (
          <p role="alert" className="text-danger mt-3 text-xs">
            {error}
          </p>
        ) : null}
      </div>
    </Panel>
  );
}
