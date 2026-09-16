import type { Metadata } from "next";
import Link from "next/link";

import { SignInPrompt } from "@/components/auth/sign-in-prompt";
import { PageBody, PageHeader } from "@/components/layout/page-header";
import { ConnectForm } from "@/components/repository/connect-form";
import { ApiErrorState } from "@/components/ui/error-state";
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
        <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1.5 text-sm">
          <Link href="/" className="text-ink-muted hover:text-ink">
            Repositories
          </Link>
          <span aria-hidden="true" className="text-ink-faint">
            /
          </span>
          <span aria-current="page" className="text-ink font-medium">
            Connect
          </span>
        </nav>
      </PageHeader>

      <PageBody>
        <div className="max-w-xl">
          <h1 className="text-ink text-2xl font-semibold tracking-tight">
            Connect a GitHub repository
          </h1>
          <p className="text-ink-muted mt-2 text-sm leading-relaxed">
            DevPilot reads the default branch through GitHub. Once it&apos;s connected, you&apos;ll
            index it so questions can be answered from the code.
          </p>

          {!integrations.ok ? (
            <ApiErrorState error={integrations.error} className="mt-6" />
          ) : !signedIn ? (
            <SignInPrompt
              className="mt-6"
              redirectPath="/repositories/connect"
              configured={github?.configured ?? false}
            />
          ) : (
            <ConnectForm className="mt-6" />
          )}
        </div>
      </PageBody>
    </>
  );
}
