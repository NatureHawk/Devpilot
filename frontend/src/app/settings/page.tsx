import type { Metadata } from "next";

import { PageBody, PageHeader, PageTitle } from "@/components/layout/page-header";
import { SettingsRow, SettingsStatus } from "@/components/settings/settings-row";
import { ThemeToggle } from "@/components/theme/theme-toggle";
import { EmptyState } from "@/components/ui/empty-state";
import { ApiErrorState } from "@/components/ui/error-state";
import { Panel, PanelHeader } from "@/components/ui/panel";
import { AccountSection } from "@/components/settings/account-section";
import { getCurrentUser, getIntegrations, type Integration } from "@/lib/api";

export const metadata: Metadata = { title: "Settings" };

export default async function SettingsPage() {
  const [result, currentUser] = await Promise.all([getIntegrations(), getCurrentUser()]);
  const user = currentUser.ok ? currentUser.data : null;
  const integrations: Integration[] = result.ok ? result.data.integrations : [];
  const github = integrations.find((integration) => integration.name === "github");
  const aiProvider = integrations.find((integration) => integration.name === "ai_provider");

  return (
    <>
      <PageHeader>
        <PageTitle>Settings</PageTitle>
      </PageHeader>

      <PageBody className="[&_section]:scroll-mt-6">
        <div className="space-y-6">
          {!result.ok ? <ApiErrorState error={result.error} /> : null}

          <Panel>
            <PanelHeader title="Account" />
            <AccountSection user={user} githubConfigured={github?.configured ?? false} />
          </Panel>

          <Panel>
            <PanelHeader title="Appearance" />
            <div className="divide-line divide-y">
              <SettingsRow
                label="Theme"
                description="Dark is the default. System follows your operating system setting. The choice is stored in this browser."
                control={<ThemeToggle />}
              />
            </div>
          </Panel>

          <Panel>
            <PanelHeader title="GitHub connection" />
            <div className="divide-line divide-y">
              <SettingsRow
                label="Credentials"
                description={
                  github?.configured
                    ? "This deployment has GitHub credentials configured in its environment."
                    : "Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET in the backend environment, then restart the API."
                }
                control={
                  <SettingsStatus>
                    {github?.configured ? "Configured" : "Not configured"}
                  </SettingsStatus>
                }
              />
              <SettingsRow
                label="Authorisation"
                description={
                  user?.has_github_token
                    ? "DevPilot holds an authorised GitHub token for your account. It is encrypted at rest and used only to read repository contents."
                    : "Sign in with GitHub to authorise DevPilot to read repository contents on your behalf."
                }
                control={
                  <SettingsStatus>
                    {user?.has_github_token ? "Authorised" : "Not authorised"}
                  </SettingsStatus>
                }
              />
              <SettingsRow
                label="Scopes"
                description="DevPilot requests read access to your account and repositories. Write access is not requested in this milestone."
                control={<SettingsStatus>read:user, repo</SettingsStatus>}
              />
            </div>
          </Panel>

          <Panel>
            <PanelHeader title="Security" />
            <div className="divide-line divide-y">
              <SettingsRow
                label="Secrets"
                description="Credentials are read from the backend environment and are never sent to the browser or written to logs. The API reports only whether an integration is configured."
                control={<SettingsStatus>Environment</SettingsStatus>}
              />
              <SettingsRow
                label="AI provider"
                description={
                  aiProvider?.configured
                    ? "An AI provider key is present. It is used only once retrieval and answering are implemented."
                    : "No AI provider key is configured. Repository-grounded answers require one."
                }
                control={
                  <SettingsStatus>
                    {aiProvider?.configured ? "Configured" : "Not configured"}
                  </SettingsStatus>
                }
              />
              <SettingsRow
                label="Repository access"
                description="Indexing only reads. Repository content is treated as untrusted data: nothing from a repository is executed, installed, or built."
                control={<SettingsStatus>Read only</SettingsStatus>}
              />
            </div>
          </Panel>

          <Panel>
            <PanelHeader title="Preferences" />
            <EmptyState
              align="start"
              className="px-5 py-8"
              title="No preferences to configure yet"
              description="Preferences appear alongside the behaviour they control — retrieval depth, diff presentation, and how pull requests are named and described."
            />
          </Panel>
        </div>
      </PageBody>
    </>
  );
}
