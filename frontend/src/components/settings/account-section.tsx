"use client";

import { useTransition } from "react";

import { signInWithGitHub, signOut } from "@/app/actions";
import { SettingsRow, SettingsStatus } from "@/components/settings/settings-row";
import { Button } from "@/components/ui/button";
import type { CurrentUser } from "@/lib/api";

/** Account rows, including the only two auth actions that exist. */
export function AccountSection({
  user,
  githubConfigured,
}: {
  user: CurrentUser | null;
  githubConfigured: boolean;
}) {
  const [pending, startTransition] = useTransition();

  return (
    <div className="divide-line divide-y">
      <SettingsRow
        label="Identity"
        description={
          user
            ? "Your DevPilot account is the GitHub account you signed in with."
            : "DevPilot signs you in with GitHub. Until then, no repositories can be connected."
        }
        control={
          user ? (
            <SettingsStatus>{user.github_login ?? "Signed in"}</SettingsStatus>
          ) : (
            <SettingsStatus>Not signed in</SettingsStatus>
          )
        }
      />
      <SettingsRow
        label="Session"
        description={
          user
            ? "Signing out clears the session cookie in this browser. Your stored GitHub token is kept so you can sign back in."
            : githubConfigured
              ? "Sign in to connect repositories and index them."
              : "GitHub sign-in is unavailable until this deployment has GitHub credentials."
        }
        control={
          user ? (
            <Button
              variant="secondary"
              size="sm"
              disabled={pending}
              onClick={() => startTransition(async () => void (await signOut()))}
            >
              Sign out
            </Button>
          ) : githubConfigured ? (
            <Button
              variant="primary"
              size="sm"
              disabled={pending}
              onClick={() =>
                startTransition(async () => void (await signInWithGitHub("/settings")))
              }
            >
              Sign in with GitHub
            </Button>
          ) : (
            <SettingsStatus>Unavailable</SettingsStatus>
          )
        }
      />
    </div>
  );
}
