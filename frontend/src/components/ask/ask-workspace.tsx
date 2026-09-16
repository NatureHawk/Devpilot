"use client";

import { ConversationPane, type RecentConversation } from "@/components/ask/conversation-pane";

/** The Ask screen for one repository. */
export function AskWorkspace({
  repositoryId,
  owner,
  name,
  disabledReason,
  blockedAction,
  recentConversations,
  initialConversationId,
}: {
  repositoryId: string | null;
  owner: string;
  name: string;
  disabledReason: string | null;
  blockedAction: { label: string; href: string } | null;
  recentConversations: RecentConversation[];
  initialConversationId: string | null;
}) {
  return (
    <ConversationPane
      repositoryId={repositoryId}
      owner={owner}
      name={name}
      disabledReason={disabledReason}
      blockedAction={blockedAction}
      recentConversations={recentConversations}
      initialConversationId={initialConversationId}
    />
  );
}
