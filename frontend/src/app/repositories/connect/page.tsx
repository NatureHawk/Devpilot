import { ArrowLeft } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { PageBody, PageHeader, PageTitle } from "@/components/layout/page-header";
import { ButtonLink } from "@/components/ui/button";
import { ApiErrorState } from "@/components/ui/error-state";
import { Panel } from "@/components/ui/panel";
import { getIntegrations } from "@/lib/api";

export const metadata: Metadata = { title: "Connect repository" };

/**
 * Connecting a repository requires the GitHub integration. Rather than showing
 * a form that cannot submit, this page reports the deployment's real
 * configuration state, which the backend answers from its own environment.
 */
export default async function ConnectRepositoryPage() {
  const result = await getIntegrations();
  const github = result.ok
    ? result.data.integrations.find((integration) => integration.name === "github")
    : undefined;

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
            DevPilot reads repositories through GitHub. Authorising the connection lets it fetch
            file contents, follow the default branch, and later open pull requests on your behalf.
          </p>
        </div>

        {!result.ok ? (
          <ApiErrorState error={result.error} className="mt-6" />
        ) : (
          <Panel className="mt-6 max-w-2xl">
            <div className="px-5 py-5">
              <div className="flex items-center justify-between gap-4">
                <h3 className="text-ink text-sm font-medium">GitHub</h3>
                <span className="text-ink-faint text-xs">
                  {github?.configured ? "Configured" : "Not configured"}
                </span>
              </div>
              <p className="text-ink-muted mt-2 text-sm leading-relaxed">
                {github?.configured
                  ? "This deployment has GitHub credentials. The authorisation flow that turns them into a connected repository is the next milestone, so no repository can be added yet."
                  : "This deployment has no GitHub credentials. Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET in the backend environment, then restart the API."}
              </p>
              <div className="mt-5">
                <ButtonLink href="/settings" variant="secondary" size="sm">
                  Open GitHub settings
                </ButtonLink>
              </div>
            </div>
          </Panel>
        )}
      </PageBody>
    </>
  );
}
