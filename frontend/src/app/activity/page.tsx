import type { Metadata } from "next";

import { PageBody, PageHeader, PageTitle } from "@/components/layout/page-header";
import { EmptyState } from "@/components/ui/empty-state";
import { Panel } from "@/components/ui/panel";

export const metadata: Metadata = { title: "Activity" };

export default function ActivityPage() {
  return (
    <>
      <PageHeader>
        <PageTitle>Activity</PageTitle>
      </PageHeader>

      <PageBody>
        <Panel>
          <EmptyState
            title="No activity yet"
            description="Activity records what DevPilot did and when: repositories indexed, questions answered, changes proposed, and pull requests opened. Nothing is recorded until a repository is connected."
          />
        </Panel>
      </PageBody>
    </>
  );
}
