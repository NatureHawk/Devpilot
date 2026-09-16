import { ButtonLink } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";

/**
 * Shown on every repository screen when the address does not match a connected
 * repository. The URL is a valid destination — it is the connection that is
 * missing, so the one action is to make it.
 */
export function NotConnectedState({ owner, repo }: { owner: string; repo: string }) {
  return (
    <EmptyState
      title={`${owner}/${repo} isn't connected`}
      description="DevPilot only reads repositories you connect. Connect it, then index it to start asking questions about its code."
    >
      <ButtonLink href="/repositories/connect" variant="primary" size="lg" forward>
        Connect repository
      </ButtonLink>
    </EmptyState>
  );
}
