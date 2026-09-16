"use client";

import { useState, useTransition } from "react";

import { signInWithGitHub } from "@/app/actions";
import { Button } from "@/components/ui/button";
import { NextStep } from "@/components/ui/next-step";

/**
 * The first step for a signed-out user.
 *
 * When GitHub OAuth is not configured for the deployment, no button is offered
 * — a control that cannot work is worse than saying what to fix.
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

  if (!configured) {
    return (
      <NextStep
        className={className}
        eyebrow="Setup needed"
        title="GitHub isn't configured yet"
        description="Add GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET to the backend environment and restart the API. Then you can connect GitHub from here."
        action={
          <Button variant="secondary" size="lg" disabled>
            Connect GitHub
          </Button>
        }
      />
    );
  }

  return (
    <NextStep
      className={className}
      title="Connect GitHub"
      description={
        <>
          Sign in with GitHub so DevPilot can read the repositories you choose. It writes nothing
          unless you approve a change and create its pull request.
          {error ? (
            <span role="alert" className="text-danger mt-2 block">
              {error}
            </span>
          ) : null}
        </>
      }
      action={
        <Button variant="primary" size="lg" onClick={start} disabled={pending}>
          {pending ? "Opening GitHub…" : "Connect GitHub"}
        </Button>
      }
    />
  );
}
