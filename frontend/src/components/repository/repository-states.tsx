import { ButtonLink } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Panel } from "@/components/ui/panel";

/**
 * Shown on every workspace tab when the address does not correspond to a
 * connected repository. The URL is honoured as a valid destination — it is the
 * repository record that is missing.
 */
export function NotConnectedState({ owner, repo }: { owner: string; repo: string }) {
  return (
    <Panel>
      <EmptyState
        title={`${owner}/${repo} is not connected`}
        description="DevPilot only reads repositories that have been connected to this workspace. Connect it to give DevPilot access to the code, then index it to make the code searchable."
      >
        <ButtonLink href="/repositories/connect" variant="primary" size="sm">
          Connect repository
        </ButtonLink>
      </EmptyState>
    </Panel>
  );
}
