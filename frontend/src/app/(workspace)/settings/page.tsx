import type { Metadata } from "next";

import { PageBody, PageHeader, PageTitle } from "@/components/layout/page-header";
import { AccountSection } from "@/components/settings/account-section";
import { SettingsRow, SettingsStatus } from "@/components/settings/settings-row";
import { ThemeToggle } from "@/components/theme/theme-toggle";
import { ApiErrorState } from "@/components/ui/error-state";
import { Panel, PanelHeader } from "@/components/ui/panel";
import { getCurrentUser, getIntegrations, type Integration } from "@/lib/api";

export const metadata: Metadata = { title: "Settings" };

export default async function SettingsPage() {
  const [result, currentUser] = await Promise.all([getIntegrations(), getCurrentUser()]);
  const user = currentUser.ok ? currentUser.data : null;
  const integrations: Integration[] = result.ok ? result.data.integrations : [];
  const find = (name: Integration["name"]) =>
    integrations.find((integration) => integration.name === name);
  const github = find("github");
  const aiProvider = find("ai_provider");
  const embeddings = find("embeddings");

  return (
    <>
      <PageHeader>
        <PageTitle>Settings</PageTitle>
      </PageHeader>

      <PageBody>
        <div className="max-w-3xl space-y-6">
          {!result.ok ? <ApiErrorState error={result.error} /> : null}

          <Panel>
            <PanelHeader title="Account" />
            <AccountSection user={user} githubConfigured={github?.configured ?? false} />
          </Panel>

          <Panel>
            <PanelHeader title="Appearance" />
            <SettingsRow
              label="Theme"
              description="Dark is the default. System follows your operating system. The choice is stored in this browser."
              control={<ThemeToggle />}
            />
          </Panel>

          <Panel>
            <PanelHeader title="GitHub" />
            <div className="divide-line divide-y">
              <SettingsRow
                label="Credentials"
                description={
                  github?.configured
                    ? "This deployment has GitHub OAuth credentials."
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
                    ? "DevPilot holds an authorised token for your account, encrypted at rest."
                    : "Connect GitHub to let DevPilot read the repositories you choose."
                }
                control={
                  <SettingsStatus>
                    {user?.has_github_token ? "Authorised" : "Not authorised"}
                  </SettingsStatus>
                }
              />
              <SettingsRow
                label="Access"
                description="DevPilot reads repository contents. It writes only when you create a pull request from a change you approved: one new branch, one commit, one pull request."
                control={<SettingsStatus>read:user, repo</SettingsStatus>}
              />
            </div>
          </Panel>

          <Panel>
            <PanelHeader title="Search and answers" />
            <div className="divide-line divide-y">
              <SettingsRow
                label="Code search"
                description={
                  embeddings?.configured
                    ? "An embedding provider is configured, so indexed repositories can be searched by meaning."
                    : "No embedding provider is configured. Indexing and search need one."
                }
                control={
                  <SettingsStatus>
                    {embeddings?.configured ? "Configured" : "Not configured"}
                  </SettingsStatus>
                }
              />
              <SettingsRow
                label="Language model"
                description={
                  aiProvider?.configured
                    ? "A language model is configured for answers and investigations."
                    : "No language model is configured. Asking and investigating need one."
                }
                control={
                  <SettingsStatus>
                    {aiProvider?.configured ? "Configured" : "Not configured"}
                  </SettingsStatus>
                }
              />
            </div>
          </Panel>

          <Panel>
            <PanelHeader title="Security" />
            <div className="divide-line divide-y">
              <SettingsRow
                label="Secrets"
                description="Credentials stay in the backend environment. They are never sent to the browser or written to logs."
                control={<SettingsStatus>Server only</SettingsStatus>}
              />
              <SettingsRow
                label="Repository content"
                description="Code is treated as untrusted data. Nothing from a repository is executed, installed or built."
                control={<SettingsStatus>Read, never run</SettingsStatus>}
              />
            </div>
          </Panel>
        </div>
      </PageBody>
    </>
  );
}
