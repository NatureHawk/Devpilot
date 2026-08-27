"use client";

import { useCallback, useState } from "react";

import { ContextPanel } from "@/components/ask/context-panel";
import { ContextRail } from "@/components/ask/context-rail";
import { ConversationPane } from "@/components/ask/conversation-pane";
import type { Repository } from "@/lib/api";
import type { AskSource } from "@/lib/ask-stream";

/**
 * Holds the state the three panes share.
 *
 * Sources belong here rather than in the conversation, because both the answer
 * and the context panel address the same evidence: clicking a citation in one
 * selects it in the other.
 */
export function AskWorkspace({
  repository,
  repositoryId,
  disabledReason,
}: {
  repository: Repository | null;
  repositoryId: string | null;
  disabledReason: string | null;
}) {
  const [sources, setSources] = useState<AskSource[]>([]);
  const [activeLabel, setActiveLabel] = useState<string | null>(null);

  const handleSources = useCallback((next: AskSource[]) => {
    setSources(next);
    setActiveLabel(null);
  }, []);

  const handleSelect = useCallback((label: string) => {
    setActiveLabel((current) => (current === label ? null : label));
  }, []);

  return (
    <div className="flex min-h-0 flex-1">
      <ContextRail repository={repository} />
      <ConversationPane
        repositoryId={repositoryId}
        disabledReason={disabledReason}
        onSourcesChange={handleSources}
        activeLabel={activeLabel}
        onSelectSource={handleSelect}
      />
      <ContextPanel
        sources={sources}
        activeLabel={activeLabel}
        onSelectSource={handleSelect}
      />
    </div>
  );
}
