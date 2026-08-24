import type { Metadata } from "next";

import { PageBody, PageHeader, PageTitle } from "@/components/layout/page-header";
import { SettingsRow, SettingsStatus } from "@/components/settings/settings-row";
import { ThemeToggle } from "@/components/theme/theme-toggle";
import { EmptyState } from "@/components/ui/empty-state";
import { ApiErrorState } from "@/components/ui/error-state";
import { Panel, PanelHeader } from "@/components/ui/panel";
import { getIntegrations, type Integration } from "@/lib/api";

export const metadata: Metadata = { title: "Settings" };

export default async function SettingsPage() {
  const result = await getIntegrations();
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
            <div className="divide-line divide-y">
              <SettingsRow
                label="Workspace"
                description="DevPilot currently runs as a single local workspace. Accounts, sign-in and shared workspaces arrive with authentication."
                control={<SettingsStatus>Local</SettingsStatus>}
              />
              <SettingsRow
                label="Identity"
                description="Your GitHub identity becomes the account identity once the GitHub connection is authorised."
                control={<SettingsStatus>Not signed in</SettingsStatus>}
              />
            </div>
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
                description="Authorising the connection is what lets DevPilot read repository contents and open pull requests. The flow is not implemented yet, so no repository can be connected."
                control={<SettingsStatus>Unavailable</SettingsStatus>}
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
                description="Write access is scoped to branches DevPilot creates. It never pushes to a default branch, and every change passes through review first."
                control={<SettingsStatus>Branch only</SettingsStatus>}
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
