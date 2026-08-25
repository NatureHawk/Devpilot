import { ArrowLeft } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { ConnectForm } from "@/components/repository/connect-form";
import { SignInPrompt } from "@/components/auth/sign-in-prompt";
import { PageBody, PageHeader, PageTitle } from "@/components/layout/page-header";
import { ApiErrorState } from "@/components/ui/error-state";
import { Panel } from "@/components/ui/panel";
import { getCurrentUser, getIntegrations } from "@/lib/api";

export const metadata: Metadata = { title: "Connect repository" };

export default async function ConnectRepositoryPage() {
  const [integrations, user] = await Promise.all([getIntegrations(), getCurrentUser()]);

  const github = integrations.ok
    ? integrations.data.integrations.find((integration) => integration.name === "github")
    : undefined;
  const signedIn = user.ok ? user.data : null;

  return (
    <>
      <PageHeader>
        <Link
          href="/repositories"
          className="text-ink-muted hover:text-ink flex items-center gap-1.5 text-sm"
        >
          <ArrowLeft aria-hidden="true" className="size-3.5" strokeWidth={1.75} />
          Repositories
        </Link>
        <span aria-hidden="true" className="text-ink-faint">
          /
        </span>
        <PageTitle>Connect</PageTitle>
      </PageHeader>

      <PageBody>
        <div className="max-w-2xl">
          <h2 className="text-ink text-xl font-medium tracking-tight">Connect a repository</h2>
          <p className="text-ink-muted mt-2 text-sm leading-relaxed">
            DevPilot reads repositories through GitHub. Once connected, indexing parses the default
            branch so questions can be answered from the code itself.
          </p>
        </div>

        {!integrations.ok ? (
          <ApiErrorState error={integrations.error} className="mt-6" />
        ) : !signedIn ? (
          <SignInPrompt
            className="mt-6"
            redirectPath="/repositories/connect"
            configured={github?.configured ?? false}
          />
        ) : (
          <Panel className="mt-6 max-w-2xl">
            <div className="px-5 py-5">
              <h3 className="text-ink text-sm font-medium">Repository</h3>
              <p className="text-ink-muted mt-1 text-xs leading-relaxed">
                Enter <code className="font-mono">owner/name</code>, or paste a GitHub URL. DevPilot
                reads visibility and the default branch from GitHub itself.
              </p>
              <ConnectForm className="mt-4" />
            </div>
          </Panel>
        )}
      </PageBody>
    </>
  );
}
